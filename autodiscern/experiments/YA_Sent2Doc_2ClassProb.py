import numpy as np
import pandas as pd

from typing import Dict, List, Tuple

import autodiscern.experiment as ade
from autodiscern.experiments.hierarchical_sent_doc_experiment import SentenceLevelModelRun


class Sent2Doc_2ClassProb_Experiment(ade.PartitionedExperiment):
    doc_level_info = {}

    def __init__(self, name: str, data_dict: Dict, model, hyperparams: Dict,
                 doc_level_info: Dict, n_partitions: int = 5, stratified=True,
                 verbose=False):
        super().__init__(name, data_dict, model, hyperparams, n_partitions, stratified, verbose)
        Sent2Doc_2ClassProb_Experiment._assign_vars(doc_level_info)

    @classmethod
    def _assign_vars(cls, doc_level_info):
        cls.doc_level_info = doc_level_info

    @classmethod
    def run_experiment_on_one_partition(cls, data_dict: Dict, partition_ids: List[int], model, hyperparams: Dict):

        train_set, test_set = cls.materialize_partition(partition_ids, data_dict)

        # run SentenceLevelModel
        sl_mr = SentenceLevelModelRun(train_set=train_set, test_set=test_set, model=model, hyperparams=hyperparams,
                                      data_dict=data_dict, doc_level_info=cls.doc_level_info)

        sl_mr.run()

        print("len(sl_mr.train_set_upd): ", len(sl_mr.train_set_upd))
        # use predictions from SentenceLevelModel to create training set for SentenceToDocModel
        data_set_train = cls.create_sent_to_doc_data_set(sl_mr.model, sl_mr.x_train, sl_mr.train_set_upd)
        data_set_test = cls.create_sent_to_doc_data_set(sl_mr.model, sl_mr.x_test, sl_mr.test_set_upd)

        dl_mr = SentenceToDocProbaModelRun(train_set=data_set_train, test_set=data_set_test, model=model,
                                           hyperparams=hyperparams)
        dl_mr.run()

        return {'sentence_level': sl_mr,
                'doc_level': dl_mr}

    @classmethod
    def create_sent_to_doc_data_set(cls, model, x_feature_set, data_set: List):
        cat_prediction = model.predict(x_feature_set)
        proba_prediction = model.predict_proba(x_feature_set)
        new_data_set = pd.DataFrame({
            'doc_id': [d['entity_id'] for d in data_set],
            'sub_id': [d['sub_id'] for d in data_set],
            'sub_prediction': cat_prediction,
            'proba_0': [i[0] for i in proba_prediction],
            'proba_1': [i[1] for i in proba_prediction],
            'label': [d['label'] for d in data_set],
        })
        return new_data_set

    def show_feature_importances(self, level):
        all_feature_importances = pd.DataFrame()
        for partition_id in self.model_runs:
            partition_feature_importances = self.model_runs[partition_id][level].get_feature_importances()
            partition_feature_importances.columns = ['partition{}'.format(partition_id)]
            all_feature_importances = pd.merge(all_feature_importances, partition_feature_importances, how='outer',
                                               left_index=True, right_index=True)
        all_feature_importances['median'] = all_feature_importances.median(axis=1)
        return all_feature_importances.sort_values('median', ascending=False)


class SentenceToDocProbaModelRun(ade.ModelRun):

    @classmethod
    def build_features(cls, train_set: pd.DataFrame, test_set: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List,
                                                                                      List, List, Dict]:

        x_train = cls.sents_to_doc_buckets_mean(train_set)
        x_test = cls.sents_to_doc_buckets_mean(test_set)

        y_train = train_set.groupby('doc_id')['label'].median()
        y_test = test_set.groupby('doc_id')['label'].median()

        feature_cols = x_train.columns
        encoders = {}

        return x_train, x_test, y_train, y_test, feature_cols, encoders

    @classmethod
    def sents_to_doc_buckets_mean(cls, df):
        series_of_list_of_bucket_avgs = df.groupby('doc_id')['sub_prediction'].apply(calc_avg_over_ten_parts)
        df = series_of_list_to_df_columns(series_of_list_of_bucket_avgs)
        return df


def mean_0_when_empty(p):
    if len(p) == 0:
        return 0
    else:
        return sum(p)/len(p)


def calc_avg_over_equal_parts(the_list, n_partitions):
    n = int(np.ceil(len(the_list)/n_partitions))
    partitions = [the_list[i*n:i*n+n] for i in range(n_partitions)]
    return [mean_0_when_empty(p) for p in partitions]


def calc_avg_over_ten_parts(the_list):
    return calc_avg_over_equal_parts(the_list, 10)


def series_of_list_to_df_columns(a_series_of_lists):
    df = pd.DataFrame()
    for ix in a_series_of_lists.index:
        d = {}
        #     d['doc_id'] = ix
        for col_i, value in enumerate(a_series_of_lists[ix]):
            d[col_i] = value
        df = pd.concat([df, pd.DataFrame(d, index=[ix])])
    return df
