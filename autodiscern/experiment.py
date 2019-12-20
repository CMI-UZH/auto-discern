import numpy as np
import pandas as pd
import random
from scipy.sparse import coo_matrix
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import RFECV
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold
from sklearn.metrics import f1_score
from sklearn.svm import SVC
from typing import Dict, List, Tuple, Callable
from autodiscern.predictor import Predictor


category_key = {
    2: 'breast cancer',
    4: 'arthritis',
    5: 'depression',
}


class PartitionedExperiment:

    def __init__(self, name: str, data_dict: Dict, label_key: str, preprocessing_func: Callable,
                 model_run_class: "ModelRun", model, hyperparams: Dict, n_partitions: int = 5, stratify_by='label',
                 feature_subset=None, reduce_features=False, verbose=False):
        """

        Args:
            name: Name for identification of the Experiment.
            data_dict: Data to run the model on.
            label_key: the key to use to calculate the label.
            preprocessing_func: the function that was used to preprocess `data_dict`
            model_run_class: An implemented subclass of ModelRun.
            model: A model with a fit() method.
            hyperparams: Dictionary of hyperparamters to search for the best model.
            n_partitions: Number of partitions to split the data on and run the experiment on.
            stratify_by: Whether to stratify the partitions by label, disease category, or None.
            feature_subset: Subset of features to use. If not used, None is passed.
            reduce_features: Whether to use recursive feature elimination to reduce features.
            verbose: Whether to display verbose messages.
        """

        if n_partitions < 2:
            raise ValueError("n_partitions must be greater than 1. Got {}.".format(n_partitions))

        self.name = name
        self.data_dict = data_dict
        self.label_key = label_key
        self.preprocessing_func = preprocessing_func
        self.verbose = verbose
        self.model_run_class = model_run_class
        self.model = model
        self.hyperparams = hyperparams
        self.feature_subset = feature_subset
        self.reduce_features = reduce_features

        self.n_partitions = n_partitions

        document_ids, doc_labels = self.build_doc_id_and_label_lists(data_dict, model_run_class, label_key)
        if stratify_by == 'label':
            self.partitions_by_ids = self.partition_document_ids_stratified(document_ids, doc_labels, self.n_partitions)
        elif stratify_by == 'category':
            doc_categories = [self.data_dict[doc_id]['categoryName'] for doc_id in self.data_dict]
            self.partitions_by_ids = self.partition_document_ids_by_category(document_ids, doc_categories, category_key)
        else:
            self.partitions_by_ids = self.partition_document_ids(document_ids, self.n_partitions)

        self.model_runs = {}
        self.all_run_results = []
        self.experiment_results = []

        if verbose:
            print("Partition Stats for {}".format(self.name))
            self.report_partition_stats(self.partitions_by_ids, document_ids, doc_labels)

    def run(self, num_partitions_to_run=None, run_hyperparam_search=True):
        """
        Run the experiment.

        Args:
            num_partitions_to_run: select a subset of partitions to run, for faster testing.
            run_hyperparam_search: argument to turn off hyperparam search, for faster testing.

        Returns:

        """
        partitions_to_run = list(self.partitions_by_ids.keys())
        if num_partitions_to_run is not None:
            partitions_to_run = partitions_to_run[:num_partitions_to_run]
            print("Running only partitions {}".format(", ".join(partitions_to_run)))

        for partition_name in self.partitions_by_ids:
            if partition_name in partitions_to_run:
                print("Running partition {}...".format(partition_name))
                model_run = self.run_experiment_on_one_partition(data_dict=self.data_dict,
                                                                 label_key=self.label_key,
                                                                 partition_ids=self.partitions_by_ids[partition_name],
                                                                 preprocessing_func=self.preprocessing_func,
                                                                 model_run_class=self.model_run_class,
                                                                 model=self.model,
                                                                 hyperparams=self.hyperparams,
                                                                 run_hyperparam_search=run_hyperparam_search,
                                                                 feature_subset=self.feature_subset,
                                                                 reduce_features=self.reduce_features)
                self.model_runs[partition_name] = model_run

        print("Compiling results")
        self.experiment_results = self.summarize_runs(self.model_runs)
        return self.experiment_results

    @classmethod
    def run_experiment_on_one_partition(cls, data_dict: Dict, label_key: str, partition_ids: List[int],
                                        preprocessing_func: Callable, model_run_class: "ModelRun", model,
                                        hyperparams: Dict, run_hyperparam_search: bool, feature_subset: List[str],
                                        reduce_features: bool):
        train_set, test_set = cls.materialize_partition(partition_ids, data_dict)
        mr = model_run_class(train_set=train_set, test_set=test_set, label_key=label_key, model=model,
                             preprocessing_func=preprocessing_func, hyperparams=hyperparams,
                             feature_subset=feature_subset, reduce_features=reduce_features)
        mr.run(run_hyperparam_search=run_hyperparam_search)
        return mr

    @staticmethod
    def build_doc_id_and_label_lists(data_dict: Dict[int, Dict], model_run_class, label_key):
        """
        Build a list of document ids and list of corresponding labels.
        Needs to work for both doc and sentence data sets! i.e. regardless of whether the key in data_dict is the same
          as the entity_id entry
        Args:
            data_dict: Dictionary of the dataset (data dicts)
            model_run_class: The model run class to use to compute the y vector
            label_key: the label key to compute the target label for

        Returns:

        """
        document_labels = {}
        for d in data_dict:
            document_labels[data_dict[d]['entity_id']] = model_run_class.build_y_vector([data_dict[d]], label_key)
        document_ids = list(document_labels.keys())
        doc_labels = [document_labels[id] for id in document_ids]
        return document_ids, doc_labels

    @classmethod
    def partition_document_ids(cls, doc_list: List[int], n: int) -> Dict[str, List[int]]:
        """Randomly shuffle and split the doc_list into n roughly equal lists."""
        random.shuffle(doc_list)
        return {'Partition {}'.format(i): doc_list[i::n] for i in range(n)}

    @classmethod
    def partition_document_ids_stratified(cls, doc_list: List[int], label_list: List[int], n: int) -> \
            Dict[str, List[int]]:
        """Randomly shuffle and split the doc_list into n roughly equal lists, stratified by label."""
        skf = StratifiedKFold(n_splits=n, random_state=42, shuffle=True)
        x = np.zeros(len(label_list))  # split takes a X argument for backwards compatibility and is not used
        partition_indexes = [test_index for train_index, test_index in skf.split(x, label_list)]
        partitions = {}
        for p_id, p in enumerate(partition_indexes):
            partitions['Partition {}'.format(p_id)] = [doc_list[i] for i in p]
        return partitions

    @classmethod
    def partition_document_ids_by_category(cls, doc_list: List[int], category_list: List[int],
                                           category_key: Dict[int, str]) -> Dict[str, List[int]]:
        """
        Partition the documents by their disease category, to test generalizability.

        Args:
            doc_list: list of the document ids
            category_list: list of each document's category id, in the same order as doc_list
            category_key: mapping for the category ids

        Returns: List of List of ints, representing each partition
        """
        categories = np.unique(category_list)
        partitions = {}
        for cat_id in categories:
            partitions[category_key[cat_id]] = [doc_list[i] for i, val in enumerate(category_list) if val == cat_id]
        return partitions

    @classmethod
    def materialize_partition(cls, partition_ids: List[int], data_dict: Dict) -> Tuple[List[Dict], List[Dict]]:
        """Create trainng and testing dataset based on the partition, which indicated the ids for the test set."""

        train_set = [data_dict[d] for d in data_dict if data_dict[d]['entity_id'] not in partition_ids]
        test_set = [data_dict[d] for d in data_dict if data_dict[d]['entity_id'] in partition_ids]

        return train_set, test_set

    @classmethod
    def report_partition_stats(cls, partitions_by_ids: Dict[str, List[int]], document_ids: List[int],
                               doc_labels: List[int]):
        labels_dict = {}
        for i, doc_id in enumerate(document_ids):
            labels_dict[doc_id] = doc_labels[i]

        for partition_name in partitions_by_ids:
            p_ids = partitions_by_ids[partition_name]
            labels_train = pd.DataFrame([labels_dict[d] for d in document_ids if d not in p_ids])
            labels_test = pd.DataFrame([labels_dict[d] for d in document_ids if d in p_ids])

            print('\n-Partition {}-'.format(partition_name))
            print("Train: {:,.0f} data points".format(labels_train.shape[0]))
            print("Test: {:,.0f} data points".format(labels_test.shape[0]))

            train_positive = labels_train[labels_train[0] == 1].shape[0] / labels_train.shape[0]
            train_negative = labels_train[labels_train[0] == 0].shape[0] / labels_train.shape[0]
            test_positive = labels_test[labels_test[0] == 1].shape[0] / labels_test.shape[0]
            test_negative = labels_test[labels_test[0] == 0].shape[0] / labels_test.shape[0]

            print("Train Set: {:.0%} pos - {:.0%} neg".format(train_positive, train_negative))
            print("Test Set: {:.0%} pos - {:.0%} neg".format(test_positive, test_negative))

    @classmethod
    def summarize_runs(cls, run_results: Dict):
        first_key_name = list(run_results.keys())[0]
        if issubclass(type(run_results[first_key_name]), ModelRun):
            return [run_results[mr].evaluation for mr in run_results]
        elif type(run_results[first_key_name]) == dict:
            return [{key: run_results[mr][key].evaluation} for mr in run_results for key in run_results[mr]]
        else:
            print("Unknown type stored in passed run_results: {}".format(type(run_results[first_key_name])))

    def show_feature_importances(self):
        all_feature_importances = pd.DataFrame()
        for partition_id in self.model_runs:
            partition_feature_importances = self.model_runs[partition_id].get_feature_importances()

            # if the model has no feature importances, just exit now
            if partition_feature_importances.shape[0] == 0:
                return pd.DataFrame()

            partition_feature_importances.columns = [partition_id]
            all_feature_importances = pd.merge(all_feature_importances, partition_feature_importances, how='outer',
                                               left_index=True, right_index=True)
        all_feature_importances['median'] = all_feature_importances.median(axis=1)
        return all_feature_importances.sort_values('median', ascending=False)

    def get_selected_hyperparams(self):
        all_hyperparams = pd.DataFrame()
        for partition_id in self.model_runs:
            partition_hyperparams = self.model_runs[partition_id].get_selected_hyperparams(partition_id)
            all_hyperparams = pd.concat([all_hyperparams, partition_hyperparams])
        return all_hyperparams

    def show_evaluation(self, metric: str = 'accuracy'):
        all_accuracy = {}
        for partition_id in self.model_runs:
            all_accuracy[partition_id] = self.model_runs[partition_id].evaluation[metric]
        all_accuracy_df = pd.DataFrame(all_accuracy, index=[self.name])
        median = all_accuracy_df.median(axis=1)
        mean = all_accuracy_df.mean(axis=1)
        stddev = all_accuracy_df.std(axis=1)
        all_accuracy_df['mean'] = mean
        all_accuracy_df['median'] = median
        all_accuracy_df['stddev'] = stddev
        return all_accuracy_df.sort_values('median', ascending=False)

    def show_accuracy(self):
        res = {}
        for level in ('doc_level', 'sentence_level'):
            res[level] = self.show_accuracy_perlevel(level)
        return res

    def show_accuracy_perlevel(self, level):
        metric = 'accuracy'
        all_accuracy = {}
        for partition_id in self.model_runs:
            all_accuracy['partition{}'.format(partition_id)] = self.model_runs[partition_id][level].evaluation[metric]
        all_accuracy_df = pd.DataFrame(all_accuracy, index=[self.name])
        median = all_accuracy_df.median(axis=1)
        mean = all_accuracy_df.mean(axis=1)
        stddev = all_accuracy_df.std(axis=1)
        all_accuracy_df['mean'] = mean
        all_accuracy_df['median'] = median
        all_accuracy_df['stddev'] = stddev
        return all_accuracy_df.sort_values('median', ascending=False)

    def generate_predictor(self, partition=0):
        """
        Return a Predictor from the trained model of a specific partition.

        Args:
            partition: The partition id of the model to return. Defaults to 0.

        Returns: a Predictor.

        """
        return self.model_runs[partition].generate_predictor()


class ModelRun:

    def __init__(self, train_set: List[Dict], test_set: List[Dict], label_key: str, model, hyperparams: Dict,
                 preprocessing_func: Callable, feature_subset=None, reduce_features=False):
        self.train_set = train_set
        self.test_set = test_set
        self.label_key = label_key
        self.encoders = {}
        self.model = clone(model)
        self.hyperparams = hyperparams
        self.feature_subset = feature_subset
        self.reduce_features = reduce_features

        self.x_train = None
        self.x_test = None
        self.y_train = None
        self.y_test = None
        self.y_train_predicted = None
        self.y_test_predicted = None
        self.feature_cols = []
        self.evaluation = None
        self.rfecv = None

        self.preprocessing_func = preprocessing_func

    def run(self, run_hyperparam_search: bool = True):
        self.x_train, self.x_test, self.y_train, self.y_test, self.feature_cols, self.encoders = self.build_data(
            self.train_set, self.test_set, self.label_key, self.feature_subset)
        if run_hyperparam_search:
            self.model = self.search_hyperparameters(self.model, self.hyperparams, self.x_train, self.y_train)
            if self.reduce_features:
                self.rfecv = self.train_feature_reducer(self.model, self.x_train, self.y_train)
                self.model = self.rfecv.estimator_
                self.feature_cols = self.rebuild_feature_cols_from_rfecv(self.feature_cols, self.rfecv.support_)
        if self.reduce_features:
            self.y_train_predicted = self.rfecv.predict(self.x_train)
            self.y_test_predicted = self.rfecv.predict(self.x_test)
            self.evaluation = self.evaluate_model(self.rfecv, self.x_test, self.y_test, self.y_test_predicted)
        else:
            self.model.fit(self.x_train, self.y_train)
            self.y_train_predicted = self.model.predict(self.x_train)
            self.y_test_predicted = self.model.predict(self.x_test)
            self.evaluation = self.evaluate_model(self.model, self.x_test, self.y_test, self.y_test_predicted)
        return self.evaluation

    @classmethod
    def rebuild_feature_cols_from_rfecv(cls, feature_cols, support):
        new_feature_cols = []
        for i, val in enumerate(support):
            if val:
                new_feature_cols.append(feature_cols[i])
        return new_feature_cols

    @classmethod
    def build_data(cls, train_set: List[Dict], test_set: List[Dict], label_key: str, feature_subset: List[str]) -> \
            Tuple[coo_matrix, coo_matrix, List, List, List, Dict]:
        """Orchestrates the construction of train and test x matrices, and train and test y vectors.

        `build_data` takes as input:
            - train_set: List
            - test_set: List
            - label_key: str. key to use in data dicts for label
            - feature_subset: List of str, or None if NA. Subset of features to use.

        `build_data` returns a Tuple of the following:
            - x_train: Matrix
            - x_test: Matrix
            - y_train: List
            - y_test: List
            - feature_cols: List
            - encoders: Dict. Encoders used to generate the feature set. Encoders that may want to be saved include
            vectorizers trained on the train_set and applied to the test_set.

        """

        encoders = cls.train_encoders(train_set)

        x_train, feature_cols = cls.build_x_features(train_set, encoders)
        x_test, feature_cols = cls.build_x_features(test_set, encoders)

        if feature_subset:
            x_train = cls.restrict_features_to_subset(x_train, feature_cols, feature_subset)
            x_test = cls.restrict_features_to_subset(x_test, feature_cols, feature_subset)
            feature_cols = feature_subset

        y_train = cls.build_y_vector(train_set, label_key)
        y_test = cls.build_y_vector(test_set, label_key)

        return x_train, x_test, y_train, y_test, feature_cols, encoders

    @classmethod
    def restrict_features_to_subset(cls, df, old_columns, feature_subset):
        # column_mask = [col in feature_subset for col in old_columns]
        column_mask = []
        for i, col_name in enumerate(old_columns):
            if col_name in feature_subset:
                column_mask.append(i)
        df = df.tocsr()
        print("type: {}".format(type(df)))
        # df = df[:,column_mask].todense().copy()
        df = pd.DataFrame(df[:, column_mask].todense())
        df.columns = feature_subset
        print(df.shape)
        return df

    @classmethod
    def train_encoders(cls, train_set: List[Dict]):
        """
        Placeholder function to hold the custom encoder training functionality of a ModelRun.

        Args:
            train_set: Data set to train encoders on.

        Returns:
            Dict of encoders.
        """
        raise NotImplementedError("The ModelRun class must be subclassed to be used, "
                                  "with the `train_encoders` function implemented.")

    @classmethod
    def build_x_features(cls, data_set: List[Dict], encoders: Dict):
        """
        Placeholder function to hold the custom feature building functionality of a ModelRun.

        Args:
            data_set: Data set to transform into features.
            encoders: Dict of pre-trained encoders for use in building features.

        Returns:
            Matrix-type
        """
        raise NotImplementedError("The ModelRun class must be subclassed to be used, "
                                  "with the `build_x_features` function implemented.")

    @classmethod
    def build_y_vector(cls, data_set: List[Dict], label_key: str) -> List:
        """
        Extract the labels from each data dict and compile into one y vector.
        Args:
            data_set: List of data dicts.
            label_key: The key int he data dicts under which the label is stored.

        Returns:
            Array-type
        """
        return [entity_dict[label_key] for entity_dict in data_set]

    @classmethod
    def search_hyperparameters(cls, model, hyperparams, x_train, y_train):
        random_search = RandomizedSearchCV(estimator=model, param_distributions=hyperparams, n_iter=5, cv=2, verbose=2,
                                           random_state=42, n_jobs=-1, scoring='f1_macro')
        random_search.fit(x_train, y_train)
        print(random_search.best_params_)
        return random_search.best_estimator_

    @classmethod
    def train_feature_reducer(cls, model, x_train, y_train):
        rfecv = RFECV(estimator=model, step=0.05, scoring='f1_macro', n_jobs=-1)  # , cv=StratifiedKFold)
        rfecv.fit(x_train, y_train)
        print("Optimal number of features : %d" % rfecv.n_features_)
        print("Params: {}".format(rfecv.get_params()))
        return rfecv

    @classmethod
    def evaluate_model(cls, model: Callable, x_test, y_test, y_test_predicted) -> Dict:
        """
        Calculate and return a dictionary of various evaluation metrics.

        Args:
            model: a model with a `score` method.
            x_test: input of the test set.
            y_test: true labels for the test set.
            y_test_predicted: the model's predictions for the test set.

        Returns:

        """
        accuracy = model.score(x_test, y_test)
        f1_macro = f1_score(y_test, y_test_predicted, average='macro')
        f1_micro = f1_score(y_test, y_test_predicted, average='micro')
        return {
            "accuracy": accuracy,
            "f1_macro": f1_macro,
            "f1_micro": f1_micro,
        }

    def get_feature_importances(self) -> pd.DataFrame:
        """
        Generate the feature importances of the trained model. Requires self.model to have a `feature_importances_`
        member variable.

        Returns: pd.DataFrame of feature importances in descending order.

        """
        if hasattr(self.model, 'feature_importances_'):
            fi = pd.DataFrame(self.model.feature_importances_, index=self.feature_cols)
            fi = fi.sort_values(0, ascending=False)
            return fi
        else:
            return pd.DataFrame()

    def get_selected_hyperparams(self, identifier) -> pd.DataFrame:
        model_hyperparam_func_map = {
            RandomForestClassifier: self.get_selected_random_forest_hyperparams,
            SVC: self.get_selected_svc_hyperparams
        }
        model_type = type(self.model)
        model_hyperparam_func = model_hyperparam_func_map[model_type]
        chosen_hyperparams = model_hyperparam_func()
        chosen_hyperparams['num_features'] = len(self.feature_cols)
        return pd.DataFrame(chosen_hyperparams, index=[identifier])

    def get_selected_random_forest_hyperparams(self) -> Dict:
        chosen_hyperparams = {
            'n_estimators': self.model.n_estimators,
            'max_features': self.model.max_features,
            'max_depth': self.model.max_depth,
            'min_samples_split': self.model.min_samples_split,
            'min_samples_leaf': self.model.min_samples_leaf,
            'class_weight': self.model.class_weight,
        }
        return chosen_hyperparams

    def get_selected_svc_hyperparams(self) -> Dict:
        chosen_hyperparams = self.model.get_params()
        return chosen_hyperparams

    def generate_predictor(self) -> Predictor:
        """
        Return a Predictor object from the trained model.

        Returns:
            Predictor
        """
        return Predictor(self.model, self.encoders, self.preprocessing_func, self.build_x_features)
