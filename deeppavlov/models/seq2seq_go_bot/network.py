"""
Copyright 2017 Neural Networks and Deep Learning lab, MIPT

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import json
import copy
import tensorflow as tf
import numpy as np
from tensorflow.contrib.layers import xavier_initializer

from deeppavlov.core.common.registry import register
from deeppavlov.core.common.errors import ConfigError
from deeppavlov.core.models.tf_model import TFModel
from deeppavlov.core.common.log import get_logger
from deeppavlov.models.seq2seq_go_bot.kb_attn_layer import KBAttention


log = get_logger(__name__)


@register("seq2seq_go_bot_nn")
class Seq2SeqGoalOrientedBotNetwork(TFModel):

    GRAPH_PARAMS = ['knowledge_base_size', 'source_vocab_size',
                    'target_vocab_size', 'hidden_size', 'embedding_size',
                    'kb_embedding_control_sum',
                    'kb_attention_hidden_sizes']

    def __init__(self, **params):
        # initialize parameters
        self._init_params(params)
        # build computational graph
        self._build_graph()
        # initialize session
        self.sess = tf.Session()
        # from tensorflow.python import debug as tf_debug
        # self.sess = tf_debug.TensorBoardDebugWrapperSession(self.sess, "vimary-pc:7019")
        self.global_step = 0

        self.sess.run(tf.global_variables_initializer())

        super().__init__(**params)

        if tf.train.checkpoint_exists(str(self.save_path.resolve())):
            log.info("[initializing `{}` from saved]".format(self.__class__.__name__))
            self.load()
        else:
            log.info("[initializing `{}` from scratch]".format(self.__class__.__name__))

    def _init_params(self, params):
        self.opt = {k: v for k, v in params.items()
                    if k not in ('knowledge_base_entry_embeddings',
                                 'decoder_embeddings')}

        self.kb_embedding = np.array(params['knowledge_base_entry_embeddings'])
        log.debug("recieved knowledge_base_entry_embeddings with shape = {}"
                  .format(self.kb_embedding.shape))
        self.opt['kb_embedding_control_sum'] = float(np.sum(self.kb_embedding))
        self.opt['knowledge_base_size'] = self.kb_embedding.shape[0]
        self.opt['embedding_size'] = self.kb_embedding.shape[1]
        self.decoder_embedding = np.array(params['decoder_embeddings'])
        if self.opt['embedding_size'] != self.decoder_embedding.shape[1]:
            raise ValueError("decoder embeddings should have the same dimension"
                             " as knowledge base entries' embeddings")
        self.opt['end_learning_rate'] = params.get('end_learning_rate',
                                                   params['learning_rate'])
        self.opt['decay_steps'] = params.get('decay_steps', 1000)
        self.opt['decay_power'] = params.get('decay_power', 1.)
        self.opt['optimizer'] = params.get('optimizer', 'AdamOptimizer')

        self.embedding_size = self.opt['embedding_size']
        self.kb_size = self.opt['knowledge_base_size']
        self.learning_rate = self.opt['learning_rate']
        self.end_learning_rate = self.opt['end_learning_rate']
        self.decay_steps = self.opt['decay_steps']
        self.decay_power = self.opt['decay_power']
        self.tgt_sos_id = self.opt['target_start_of_sequence_index']
        self.tgt_eos_id = self.opt['target_end_of_sequence_index']
        self.src_vocab_size = self.opt['source_vocab_size']
        self.tgt_vocab_size = self.opt['target_vocab_size']
        self.hidden_size = self.opt['hidden_size']
        self.kb_attn_hidden_sizes = self.opt['kb_attention_hidden_sizes']

        self._optimizer = None
        if hasattr(tf.train, self.opt['optimizer']):
            self._optimizer = getattr(tf.train, self.opt['optimizer'])
        if not issubclass(self._optimizer, tf.train.Optimizer):
            raise ConfigError("`optimizer` parameter should be a name of"
                              " tf.train.Optimizer subclass")

    def _build_graph(self):

        self._add_placeholders()

        _logits, self._predictions = self._build_body()

        _weights = tf.expand_dims(self._tgt_weights, -1)
        _loss_tensor = \
            tf.losses.sparse_softmax_cross_entropy(logits=_logits,
                                                   labels=self._decoder_outputs,
                                                   weights=_weights,
                                                   reduction=tf.losses.Reduction.NONE)
        # normalize loss by batch_size
        _loss_tensor = \
            tf.verify_tensor_all_finite(_loss_tensor, "Non finite values in loss tensor.")
        self._loss = tf.reduce_sum(_loss_tensor) / tf.cast(self._batch_size, tf.float32)
        # self._loss = tf.reduce_mean(_loss_tensor, name='loss')
# TODO: tune clip_norm
        self._train_op = \
            self.get_train_op(self._loss, 
                              learning_rate=self._learning_rate,
                              optimizer=self._optimizer,
                              clip_norm=10.)

    def _add_placeholders(self):
        self._learning_rate = tf.placeholder(tf.float32,
                                             shape=[],
                                             name='learning_rate')
        # _encoder_inputs: [batch_size, max_input_time]
        # _encoder_inputs: [batch_size, max_input_time, embedding_size]
        self._encoder_inputs = tf.placeholder(tf.float32,
                                              [None, None, self.embedding_size],
                                              name='encoder_inputs')
        self._batch_size = tf.shape(self._encoder_inputs)[0]
        # _decoder_inputs: [batch_size, max_output_time]
        self._decoder_inputs = tf.placeholder(tf.int32,
                                              [None, None],
                                              name='decoder_inputs')
        # _decoder_embedding: [tgt_vocab_size + kb_size, embedding_size]
        self._decoder_embedding = \
            tf.get_variable("decoder_embedding",
                            shape=(self.tgt_vocab_size + self.kb_size,
                                   self.embedding_size),
                            dtype=tf.float32,
                            initializer=tf.constant_initializer(self.decoder_embedding),
                            trainable=False)
        # _decoder_outputs: [batch_size, max_output_time]
        self._decoder_outputs = tf.placeholder(tf.int32,
                                               [None, None],
                                               name='decoder_outputs')
        # _kb_embedding: [kb_size, embedding_size]
# TODO: try training embeddings
        kb_W = np.array(self.kb_embedding)[:, :self.embedding_size]
        self._kb_embedding = tf.get_variable("kb_embedding",
                                             shape=(kb_W.shape[0], kb_W.shape[1]),
                                             dtype=tf.float32,
                                             initializer=tf.constant_initializer(kb_W),
                                             trainable=False)
        # _kb_mask: [batch_size, kb_size]
        self._kb_mask = tf.placeholder(tf.float32, [None, None], name='kb_mask')

# TODO: compute sequence lengths on the go
        # _src_sequence_lengths, _tgt_sequence_lengths: [batch_size]
        self._src_sequence_lengths = tf.placeholder(tf.int32,
                                                    [None],
                                                    name='input_sequence_lengths')
        self._tgt_sequence_lengths = tf.placeholder(tf.int32,
                                                    [None],
                                                    name='output_sequence_lengths')
        # _tgt_weights: [batch_size, max_output_time]
        self._tgt_weights = tf.placeholder(tf.int32,
                                           [None, None],
                                           name='target_weights')

    def _build_body(self):
        # Encoder embedding
        # _encoder_embedding = tf.get_variable(
        #   "encoder_embedding", [self.src_vocab_size, self.embedding_size])
        # _encoder_emb_inp = tf.nn.embedding_lookup(_encoder_embedding,
        #                                          self._encoder_inputs)
        _encoder_emb_inp = self._encoder_inputs
        # _encoder_emb_inp = tf.one_hot(self._encoder_inputs, self.src_vocab_size)

        # Decoder embedding
        # _decoder_embedding = tf.get_variable(
        #    "decoder_embedding", [self.tgt_vocab_size + self.kb_size,
        #                          self.embedding_size])
        _decoder_emb_inp = tf.nn.embedding_lookup(self._decoder_embedding,
                                                  self._decoder_inputs)
        # _decoder_emb_inp = tf.one_hot(self._decoder_inputs,
        #                              self.tgt_vocab_size + self.kb_size)

        with tf.variable_scope("Encoder"):
            _encoder_cell = tf.nn.rnn_cell.BasicLSTMCell(self.hidden_size)
            # Run Dynamic RNN
            #   _encoder_outputs: [max_time, batch_size, num_units]
            #   _encoder_state: [batch_size, num_units]
# input_states?
            _encoder_outputs, _encoder_state = tf.nn.dynamic_rnn(
                _encoder_cell, _encoder_emb_inp, dtype=tf.float32,
                sequence_length=self._src_sequence_lengths, time_major=False)

        with tf.variable_scope("Decoder"):
            _decoder_cell = tf.nn.rnn_cell.BasicLSTMCell(self.hidden_size)
            # Train Helper
            _helper = tf.contrib.seq2seq.TrainingHelper(
                _decoder_emb_inp, self._tgt_sequence_lengths, time_major=False)
            # Infer Helper
            _max_iters = tf.round(tf.reduce_max(self._src_sequence_lengths) * 2)
            _helper_infer = tf.contrib.seq2seq.GreedyEmbeddingHelper(
                self._decoder_embedding,
                tf.fill([self._batch_size], self.tgt_sos_id), self.tgt_eos_id)
            #    lambda d: tf.one_hot(d, self.tgt_vocab_size + self.kb_size),

            def decode(helper, scope, max_iters=None, reuse=None):
                with tf.variable_scope(scope, reuse=reuse):
                    with tf.variable_scope("AttentionOverKB", reuse=reuse):
                        _kb_attn_layer = KBAttention(self.tgt_vocab_size,
                                                     self.kb_attn_hidden_sizes + [1],
                                                     self._kb_embedding,
                                                     self._kb_mask,
                                                     activation=tf.nn.relu,
                                                     use_bias=False,
                                                     reuse=reuse)
# TODO: rm output dense layer
                    # Output dense layer
                    #_projection_layer = \
                    #    tf.layers.Dense(self.tgt_vocab_size, use_bias=False, _reuse=reuse)
                    # Decoder
                    _decoder = \
                        tf.contrib.seq2seq.BasicDecoder(_decoder_cell, helper,
                                                        initial_state=_encoder_state,
                                                        output_layer=_kb_attn_layer)
                    # Dynamic decoding
# TRY: impute_finished = True,
                    _outputs, _, _ = \
                        tf.contrib.seq2seq.dynamic_decode(_decoder,
                                                          impute_finished=True,
                                                          maximum_iterations=max_iters,
                                                          output_time_major=False)
                    return _outputs

            _logits = decode(_helper, "decode").rnn_output
            _predictions = \
                decode(_helper_infer, "decode", _max_iters, reuse=True).sample_id

        return _logits, _predictions

    def _multilayer_perceptron(units, hidden_dims=[], use_bias=True):
        # Hidden fully connected layers with relu activation
        for i, h in enumerate(hidden_dims):
# TODO: check stddev
            _W_init = tf.truncated_normal([units.shape[-1], h],
                                          stddev=1,)
            _W = tf.Variable(_W_init, name="W_{}".format(i))
            units = tf.matmul(units, _W)
            if use_bias:
                _b_init = tf.truncated_normal([h])
                _b = tf.Variable(_b_init, name="b_{}".format(i))
                units = tf.add(units, _b)
            units = tf.nn.relu(units)
        return units

    def __call__(self, enc_inputs, src_seq_lengths, kb_masks, prob=False):
        predictions = self.sess.run(
            self._predictions,
            feed_dict={
                self._learning_rate: 1.,
                self._encoder_inputs: enc_inputs,
                self._src_sequence_lengths: src_seq_lengths,
                self._kb_mask: kb_masks
            }
        )
# TODO: implement infer probabilities
        if prob:
            raise NotImplementedError("Probs not available for now.")
        return predictions

    def train_on_batch(self, enc_inputs, dec_inputs, dec_outputs,
                       src_seq_lengths, tgt_seq_lengths, tgt_weights, kb_masks):
        _, loss_value = self.sess.run(
            [self._train_op, self._loss],
            feed_dict={
                self._learning_rate: self.get_learning_rate(),
                self._encoder_inputs: enc_inputs,
                self._decoder_inputs: dec_inputs,
                self._decoder_outputs: dec_outputs,
                self._src_sequence_lengths: src_seq_lengths,
                self._tgt_sequence_lengths: tgt_seq_lengths,
                self._tgt_weights: tgt_weights,
                self._kb_mask: kb_masks
            }
        )
        return loss_value

    def get_learning_rate(self):
        # polynomial decay
        global_step = min(self.global_step, self.decay_steps)
        decayed_learning_rate = \
            (self.learning_rate - self.end_learning_rate) *\
            (1 - global_step / self.decay_steps) ** self.decay_power +\
            self.end_learning_rate
        return decayed_learning_rate

    def load(self, *args, **kwargs):
        self.load_params()
        super().load(*args, **kwargs)

    def load_params(self):
        path = str(self.load_path.with_suffix('.json').resolve())
        log.info('[loading parameters from {}]'.format(path))
        with open(path, 'r') as fp:
            params = json.load(fp)
        for p in self.GRAPH_PARAMS:
            if self.opt.get(p) != params.get(p):
                if p in ('kb_embedding_control_sum') and\
                        (math.abs(self.opt.get(p, 0.) - params.get(p, 0.)) < 1e-3):
                    continue
                raise ConfigError("`{}` parameter must be equal to saved model"
                                  " parameter value `{}`, but is equal to `{}`"
                                  .format(p, params.get(p), self.opt.get(p)))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.save_params()

    def save_params(self):
        path = str(self.save_path.with_suffix('.json').resolve())
        log.info('[saving parameters to {}]'.format(path))
        with open(path, 'w') as fp:
            json.dump(self.opt, fp)

    def process_event(self, event_name, data):
        if event_name == 'after_epoch':
            log.info("Updating global step, learning rate = {:.6f}."
                     .format(self.get_learning_rate()))
            self.global_step += 1

    def shutdown(self):
        self.sess.close()
