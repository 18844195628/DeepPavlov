# Copyright 2018 Neural Networks and Deep Learning lab, MIPT
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import copy
import json

from collections import Counter
from typing import List, Tuple, Dict, Any, Union
from operator import itemgetter
from scipy.stats import entropy
import numpy as np
from scipy.sparse import csr_matrix, vstack, hstack
from scipy.sparse.linalg import norm as sparse_norm
from scipy.spatial.distance import cosine, euclidean

import numpy as np

from deeppavlov.core.common.registry import register
from deeppavlov.core.common.log import get_logger
from deeppavlov.core.common.file import save_pickle, load_pickle
from deeppavlov.core.commands.utils import expand_path, make_all_dirs, is_file_exist
from deeppavlov.core.models.estimator import Component

logger = get_logger(__name__)

@register("ecommerce_tfidf_bot")
class EcommerceTfidfBot(Component):
    """Class to retrieve product items from `load_path` catalogs
    in sorted order according to the similarity measure
    Retrieve the specification attributes with corresponding values
    in sorted order according to entropy.

    Parameters:
        preprocess: text preprocessing component
        save_path: path to save a model
        load_path: path to load a model
        entropy_fields: the specification attributes of the catalog items
        min_similarity: similarity threshold for ranking
        min_entropy: min entropy threshold for specifying
    """

    def __init__(self, 
                save_path: str, 
                load_path: str, 
                entropy_fields: list, 
                min_similarity: float = 0.5,
                min_entropy: float = 0.5, 
                **kwargs) -> None:

        self.save_path = expand_path(save_path)
        self.load_path = expand_path(load_path)

        self.min_similarity = min_similarity
        self.min_entropy = min_entropy
        self.entropy_fields = entropy_fields
        self.ec_data: List = []
        if kwargs.get('mode') != 'train':
            self.load()

    def fit(self, data, query) -> None:

        # print(query)
        # pass
        """Preprocess items `title` and `description` from the `data`

        Parameters:
            data: list of catalog items

        Returns:
            None
        """
    
        self.x_train_features = vstack(list(query))
        self.ec_data = data
        
    def save(self) -> None:
        """Save classifier parameters"""
        logger.info("Saving to {}".format(self.save_path))
        path = expand_path(self.save_path)
        make_all_dirs(path)

#        print("densing")
 #       self.x_train_features = [x.todense() for x in self.x_train_features]

        save_pickle((self.ec_data, self.x_train_features), path)

    def load(self) -> None:
        """Load classifier parameters"""
        logger.info("Loading from {}".format(self.load_path))
        self.ec_data, self.x_train_features = load_pickle(expand_path(self.load_path))

 #       print("densing")
  #      self.x_train_features = [x.todense() for x in self.x_train_features]

    def __call__(self, q_vects, histories, states):
        """Retrieve catalog items according to the TFIDF measure

        Parameters:
            queries: list of queries
            history: list of previous queries
            states: list of dialog state

        Returns:
            response:   items:      list of retrieved items
                        entropies:  list of entropy attributes with corresponding values

            confidence: list of similarity scores
            state: dialog state
        """

        logger.info(f"Total catalog {len(self.ec_data)}")

        if not isinstance(q_vects, list):
            q_vects = [q_vects]

        if not isinstance(states, list):
            states = [states]

        if not isinstance(histories, list):
            histories = [histories]

        items: List = []
        confidences: List = []
        back_states: List = []
        entropies: List = []

        for idx, q_vect in enumerate(q_vects):

            logger.info(f"Search query {q_vect}")
            print(q_vect)
            # b = vstack([q_vect, q_vect])
            # print(b)
            # print(type(q_vect))
            # print(type(b))

            # print(q_vect.shape)
            # print(b.shape)     
            # q_comp = vstack([state['history'][-1],q_vect]).toarray()

            if len(states)>=idx+1:
                state = states[idx]
            else:
                state = {'start': 0, 'stop': 5}

            if 'start' not in state:
                state['start'] = 0
            if 'stop' not in state:
                state['stop'] = 5

            if 'history' not in state:
                state['history'] = []

            logger.info(f"Current state {state}")

            if len(state['history'])>0:
                if not np.array_equal(state['history'][-1].todense(), q_vect.todense()):
                    q_comp = q_vect.maximum(state['history'][-1])
                    print("complex query")
                    print(q_comp)
                    complex_bool = self._take_complex_query(q_comp, q_vect)
                    print(complex_bool)

                    if complex_bool is True:
                        q_vect = q_comp
                        state['start'] = 0
                        state['stop'] = 5
                    else:
                        # current short query wins that means that the state should be zeroed
                        state = {
                            'history': [],
                            'start': 0,
                            'stop': 5,
                            }
                else:
                    print('we have the same query')
            else:
                print('history is empty')

            state['history'].append(q_vect)

#            q_vect_dense = q_vect.todense()

            #cos_distance = [cosine(q_vect_dense, x.todense()) for x in self.x_train_features]
            # print("cosining")
            #cos_distance = [cosine(q_vect_dense, x) for x in self.x_train_features]
            # norm = sparse_norm(q_vect) * sparse_norm(self.x_train_features, axis=1)
            # cos_similarities = np.array(q_vect.dot(self.x_train_features.T).todense())/norm

            # cos_similarities = cos_similarities[0]
            # cos_similarities = np.nan_to_num(cos_similarities)
        
   #         scores = [(cos, len(self.ec_data[idx]['Title'])) for idx, cos in enumerate(cos_distance)]
    #        print("calc cosine")

    #        raw_scores = np.array(scores, dtype=[('x', 'float_'), ('y', 'int_')])

     #       answer_ids = np.argsort(raw_scores, order=('x', 'y'))
      #      print("sorted")

            print('final query')
            print(q_vect)

            scores = self._similarity(q_vect)
            answer_ids = np.argsort(scores)[::-1]
            answer_ids_filtered = [idx for idx in answer_ids if scores[idx] >= self.min_similarity]

            answer_ids = _state_based_filter(answer_ids, state)
            
            items.append([self.ec_data[idx] for idx in answer_ids[state['start']:state['stop']]])

            #confidences.append([cos_distance[idx] for idx in answer_ids[state['start']:state['stop']]])
            confidences.append([scores[idx] for idx in answer_ids[state['start']:state['stop']]])

            back_states.append(state)
            entropies.append(self._entropy_subquery(answer_ids_filtered))

        print(items)
        print(confidences)

        return (items, entropies), confidences, back_states

    def _take_complex_query(self, q_prev: csr_matrix, q_cur: csr_matrix) -> bool:
        prev_sim = self._similarity(q_prev)
        cur_sim = self._similarity(q_cur)

        if prev_sim.max()>cur_sim.max():
            return True

        return False

    def _similarity(self, q_vect: Union[csr_matrix, List]) -> List[float]:
        norm = sparse_norm(q_vect) * sparse_norm(self.x_train_features, axis=1)
        cos_similarities = np.array(q_vect.dot(self.x_train_features.T).todense())/norm

        cos_similarities = cos_similarities[0]
        cos_similarities = np.nan_to_num(cos_similarities)
        return cos_similarities

    def _state_based_filter(self, ids, state):
        for key, value in state.items():
            log.debug(f"Filtering for {key}:{value}")

            if key == 'Price':
                price = value
                log.debug(f"Items before price filtering {len(ids)} with price {price}")
                ids = [idx for idx in ids
                        if self.preprocess.price(self.ec_data[idx]) >= price[0] and
                        self.preprocess.price(self.ec_data[idx]) <= price[1] and
                        self.preprocess.price(self.ec_data[idx]) != 0]
                log.debug(f"Items after price filtering {len(ids)}")

            elif key in ['query', 'start', 'stop']:
                continue

            else:
                ids = [idx for idx in ids
                        if key in self.ec_data[idx]
                        if self.ec_data[idx][key].lower() == value.lower()]
        return ids

    def _entropy_subquery(self, results_args: List[int]) -> List[Tuple[float, str, List[Tuple[str, int]]]]:
        """Calculate entropy of selected attributes for items from the catalog.

        Parameters:
            results_args: items id to consider

        Returns:
            entropies: entropy score with attribute name and corresponding values
        """

        ent_fields: Dict = {}

        for idx in results_args:
            for field in self.entropy_fields:
                if field in self.ec_data[idx]:
                    if field not in ent_fields:
                        ent_fields[field] = []

                    ent_fields[field].append(self.ec_data[idx][field].lower())

        entropies = []
        for key, value in ent_fields.items():
            count = Counter(value)
            entropies.append(
                (entropy(list(count.values()), base=2), key, count.most_common()))

        entropies = sorted(entropies, key=itemgetter(0), reverse=True)
        entropies = [ent_item for ent_item in entropies if ent_item[0]
                     >= self.min_entropy]

        return entropies



