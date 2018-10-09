# Copyright 2017 Neural Networks and Deep Learning lab, MIPT
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import defaultdict
from typing import Iterable, Optional, Any, Union, List
from enum import IntEnum
import math

import numpy as np
import tensorflow as tf
from tensorflow.python.ops import variables

from deeppavlov.core.models.nn_model import NNModel
from deeppavlov.core.common.log import get_logger
from deeppavlov.core.common.errors import ConfigError
from deeppavlov.core.common.registry import cls_from_str
from .tf_backend import TfModelMeta


log = get_logger(__name__)


class TFModel(NNModel, metaclass=TfModelMeta):
    """Parent class for all components using TensorFlow."""
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def load(self, exclude_scopes: Optional[Iterable] = ('Optimizer',)) -> None:
        """Load model parameters from self.load_path"""
        if not hasattr(self, 'sess'):
            raise RuntimeError('Your TensorFlow model {} must'
                               ' have sess attribute!'.format(self.__class__.__name__))
        path = str(self.load_path.resolve())
        # Check presence of the model files
        if tf.train.checkpoint_exists(path):
            log.info('[loading model from {}]'.format(path))
            # Exclude optimizer variables from saved variables
            var_list = self._get_saveable_variables(exclude_scopes)
            saver = tf.train.Saver(var_list)
            saver.restore(self.sess, path)

    def save(self, exclude_scopes: Optional[Iterable] = ('Optimizer',)) -> None:
        """Save model parameters to self.save_path"""
        if not hasattr(self, 'sess'):
            raise RuntimeError('Your TensorFlow model {} must'
                               ' have sess attribute!'.format(self.__class__.__name__))
        path = str(self.save_path.resolve())
        log.info('[saving model to {}]'.format(path))
        var_list = self._get_saveable_variables(exclude_scopes)
        saver = tf.train.Saver(var_list)
        saver.save(self.sess, path)

    @staticmethod
    def _get_saveable_variables(exclude_scopes=tuple()):
        all_vars = variables._all_saveable_objects()
        vars_to_train = [var for var in all_vars if all(sc not in var.name for sc in exclude_scopes)]
        return vars_to_train

    @staticmethod
    def _get_trainable_variables(exclude_scopes=tuple()):
        all_vars = tf.global_variables()
        vars_to_train = [var for var in all_vars if all(sc not in var.name for sc in exclude_scopes)]
        return vars_to_train

    def get_train_op(self,
                     loss,
                     learning_rate,
                     optimizer=None,
                     clip_norm=None,
                     learnable_scopes=None,
                     optimizer_scope_name=None):
        """ Get train operation for given loss

        Args:
            loss: loss, tf tensor or scalar
            learning_rate: scalar or placeholder
            clip_norm: clip gradients norm by clip_norm
            learnable_scopes: which scopes are trainable (None for all)
            optimizer: instance of tf.train.Optimizer, default Adam

        Returns:
            train_op
        """
        if optimizer_scope_name is None:
            opt_scope = tf.variable_scope('Optimizer')
        else:
            opt_scope = tf.variable_scope(optimizer_scope_name)
        with opt_scope:
            if learnable_scopes is None:
                variables_to_train = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
            else:
                variables_to_train = []
                for scope_name in learnable_scopes:
                    variables_to_train.extend(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=scope_name))

            if optimizer is None:
                optimizer = tf.train.AdamOptimizer

            # For batch norm it is necessary to update running averages
            extra_update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
            with tf.control_dependencies(extra_update_ops):

                def clip_if_not_none(grad):
                    if grad is not None:
                        return tf.clip_by_norm(grad, clip_norm)

                opt = optimizer(learning_rate)
                grads_and_vars = opt.compute_gradients(loss, var_list=variables_to_train)
                if clip_norm is not None:
                    grads_and_vars = [(clip_if_not_none(grad), var)
                                      for grad, var in grads_and_vars]
                train_op = opt.apply_gradients(grads_and_vars)
        return train_op

    @staticmethod
    def print_number_of_parameters():
        """
        Print number of *trainable* parameters in the network
        """
        log.info('Number of parameters: ')
        variables = tf.trainable_variables()
        blocks = defaultdict(int)
        for var in variables:
            # Get the top level scope name of variable
            block_name = var.name.split('/')[0]
            number_of_parameters = np.prod(var.get_shape().as_list())
            blocks[block_name] += number_of_parameters
        for block_name, cnt in blocks.items():
            log.info("{} - {}.".format(block_name, cnt))
        total_num_parameters = np.sum(list(blocks.values()))
        log.info('Total number of parameters equal {}'.format(total_num_parameters))


class DecayType(IntEnum):
    ''' Data class, each decay type is assigned a number. '''
    NO = 1
    LINEAR = 2
    COSINE = 3
    EXPONENTIAL = 4
    POLYNOMIAL = 5

    @classmethod
    def from_str(cls, label: str):
        if label.upper() in cls.__members__:
            return DecayType[label.upper()]
        else:
            raise NotImplementedError


class DecayScheduler():
    '''
    Given initial and endvalue, this class generates the next value
    depending on decay type and number of iterations. (by calling next_val().)
    '''

    def __init__(self, dec_type: Union[str, DecayType], start_val: float,
                 num_it: int = None, end_val: float = None, extra: float = None):
        if isinstance(dec_type, DecayType):
            self.dec_type = dec_type
        else:
            self.dec_type = DecayType.from_str(dec_type)
        self.nb, self.extra = num_it, extra
        self.start_val, self.end_val = start_val, end_val
        self.iters = 0
        if self.end_val is None and not (self.dec_type in [1, 4]):
            self.end_val = 0

    def next_val(self):
        self.iters = min(self.iters + 1, self.nb)
        if self.dec_type == DecayType.NO:
            return self.start_val
        elif self.dec_type == DecayType.LINEAR:
            pct = self.iters / self.nb
            return self.start_val + pct * (self.end_val - self.start_val)
        elif self.dec_type == DecayType.COSINE:
            cos_out = math.cos(math.pi * self.iters / self.nb) + 1
            return self.end_val + (self.start_val - self.end_val) / 2 * cos_out
        elif self.dec_type == DecayType.EXPONENTIAL:
            ratio = self.end_val / self.start_val
            return self.start_val * (ratio ** (self.iters / self.nb))
        elif self.dec_type == DecayType.POLYNOMIAL:
            delta_val = self.start_val - self.end_val
            return self.end_val + delta_val * (1 - self.iters / self.nb) ** self.extra


class AnhancedTFModel(TFModel):
    """TFModel anhanced with optimizer, learning rate and momentum configuration"""
    def __init__(self,
                 learning_rate: Union[float, List[float]],
                 learning_rate_decay: Union[str, List[Any]] = DecayType.NO,
                 learning_rate_decay_epochs: int = 0,
                 learning_rate_decay_batches: int = 0,
                 optimizer: str = 'AdamOptimizer',
                 *args, **kwargs) -> None:
        if learning_rate_decay_epochs and learning_rate_decay_batches:
            raise ConfigError("isn't able to update learning rate every batch"
                              " and every epoch sumalteniously")
        super().__init__(*args, **kwargs)

        end_val, num_it, dec_type, extra = None, None, DecayType.NO, None
        if isinstance(learning_rate, (tuple, list)):
            start_val, end_val = learning_rate
        else:
            start_val = learning_rate
        if learning_rate_decay is not None:
            if isinstance(learning_rate_decay, (tuple, list)):
                dec_type, extra = learning_rate_decay
            else:
                dec_type = learning_rate_decay

        self._lr = start_val
        self._lr_update_on_batch = False
        if learning_rate_decay_epochs > 0:
            num_it = learning_rate_decay_epochs
        elif learning_rate_decay_batches > 0:
            num_it = learning_rate_decay_batches
            self._lr_update_on_batch = True

        log.info(f"start_val={start_val},end_val={end_val},num_it={num_it}"
                 f",dec_type={dec_type},extra={extra}")
        self._lr_schedule = DecayScheduler(start_val=start_val, end_val=end_val,
                                           num_it=num_it, dec_type=dec_type,
                                           extra=extra)

        try:
            self._optimizer = cls_from_str(optimizer)
        except (ImportError, ValueError):
            self._optimizer = getattr(tf.train, optimizer.split(':')[-1])
        if not issubclass(self._optimizer, tf.train.Optimizer):
            raise ConfigError("`optimizer` should be tensorflow.train.Optimizer subclass")

    def process_event(self, event_name, data):
        if self._lr_update_on_batch:
            if event_name == 'after_batch':
                self._lr = self._lr_schedule.next_val()
        elif event_name == 'after_epoch':
            self._lr = self._lr_schedule.next_val()

    def get_learning_rate(self):
        return self._lr

    def get_optimizer(self):
        return self._optimizer
