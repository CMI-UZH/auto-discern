import datetime
import dill
from git import Repo
import glob
import inspect
import os
import pkg_resources
import pandas as pd
import pickle
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict

import autodiscern.transformations as adt


class DataManager:
    """Data manager that loads DISCERN corpus into different formats.

       Args:
           data_path: string, path/to/data

       .. note ::

           On windows OS, the backslash in the string should be escaped!!
    """

    def __init__(self, data_path):
        expanded_path = Path(data_path).expanduser()
        if expanded_path.exists():
            self.data_path = expanded_path
        else:
            raise ValueError("Path does not exist: {}".format(data_path))
        self.data = {}

        self.DataProcessorCacheManager = DataProcessorCacheManager()

    def build_dicts(self):
        """Build a dictionary of data dictionaries, keyed on their entity_ids. """
        self._load_articles('html_articles')
        self._load_responses()
        print("Building data dicts...")
        data_list_of_dicts = self.data['html_articles'].to_dict('records')
        data_dict = adt.convert_list_of_dicts_to_dict_of_dicts(data_list_of_dicts)

        for id in data_dict:
            entity_responses = self.data['responses'][self.data['responses']['entity_id'] == id]
            responses_pivoted = pd.pivot_table(entity_responses, index='questionID', columns='uid', values='answer',
                                               aggfunc='median')
            data_dict[id]['responses'] = responses_pivoted

        ids = list(data_dict.keys())
        print(" ... {} data dicts built".format(len(ids)))
        print("Available keys: {}".format(", ".join(list(data_dict[ids[0]].keys()))))
        return data_dict

    def _load_articles(self, version_id: str) -> None:
        """Loads all files located in self.data_path/data/<version_id> into a pd.df at self.data dict[version_id]"""

        print("Loading articles...")

        articles = pd.DataFrame(columns=['entity_id', 'content'])
        articles_path = Path(self.data_path, "data/{}/*".format(version_id))

        files = glob.glob(articles_path.__str__())

        if len(files) == 0:
            print("WARNING: no files found at {}".format(articles_path))

        for file in files:
            # to enforce encoding -- it generates errors without it!
            with open(file, 'r', encoding='utf-8') as f:
                # keep what's after the last / and before the .
                entity_id = os.path.basename(file).split('.')[0]
                content = f.read()
                articles = articles.append({'entity_id': int(entity_id), 'content': content}, ignore_index=True)

        # only keep articles listed in the target_ids file
        target_ids = pd.read_csv(Path(self.data_path, "data/target_ids.csv"))
        articles['entity_id'] = articles['entity_id'].astype(int)
        articles = pd.merge(articles, target_ids, on='entity_id')

        # add the article urls
        article_urls = pd.read_csv(Path(self.data_path, "data/urls.csv"))
        articles = pd.merge(articles, article_urls, on='entity_id')

        print(" ... {} articles loaded".format(articles.shape[0]))
        self.data[version_id] = articles

    def _load_responses(self) -> None:
        print("Loading responses...")
        self.data['responses'] = pd.read_csv(Path(self.data_path, "data/responses.csv"))
        print(" ... {} responses loaded".format(self.data['responses'].shape[0]))

    def _articles(self, version) -> pd.DataFrame:
        version_id = "{}_articles".format(version)
        if version_id not in self.data:
            self._load_articles(version_id)
        return self.data[version_id]

    @staticmethod
    def get_repo_path():
        return os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))

    @classmethod
    def generate_filename_for_file(cls, tag: str = None) -> str:
        repo_path = cls.get_repo_path()
        git_hash = cls._get_git_hash(repo_path)
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        if tag:
            filename = "{}_{}_{}".format(timestamp, git_hash, tag)
        else:
            filename = "{}_{}".format(timestamp, git_hash)
        return filename

    def save_transformed_data(self, data: Dict, tag: str = None, enforce_clean_git=True) -> str:
        """Save a data dictionary to data/transformed directory with a filename created from the current timestamp and
        an optional tag.
        Getting the path based on:
        https://stackoverflow.com/questions/50499/how-do-i-get-the-path-and-name-of-the-file-that-is-currently-executing

        Args:
            data: dict. Can contain anything that's pickle-able.
            tag: str. A small description of the data for easy recognition in the file system.

        Returns: None

        """
        if enforce_clean_git:
            self.check_for_uncommitted_git_changes()

        filename = self.generate_filename_for_file(tag)
        filepath = Path(self.data_path, "data/transformed_data", "{}.pkl".format(filename))
        with open(filepath, "wb+") as f:
            pickle.dump(data, f)
        print("Saved data to {}".format(filepath))
        return filepath

    def cache_data_processor(self, data: Any, processing_func: Callable, tag: str = None, data_file_type='pkl',
                             enforce_clean_git=True) -> str:
        if enforce_clean_git:
            self.check_for_uncommitted_git_changes()

        file_name = self.generate_filename_for_file(tag)
        file_data_path = Path(self.data_path, "data/transformed_data")
        self.DataProcessorCacheManager.save(data, processing_func, file_name=file_name, data_file_type=data_file_type,
                                            file_dir_path=file_data_path)
        return file_name

    def load_transformed_data(self, filename: str) -> Dict:
        """Load a pickled data dictionary from the data/transformed directory.
        filename can be provided with or without the .pkl extension"""

        if '.pkl' in filename:
            filepath = Path(self.data_path, "data/transformed_data/{}".format(filename))
        else:
            filepath = Path(self.data_path, "data/transformed_data/{}.pkl".format(filename))
        with open(filepath, "rb+") as f:
            return pickle.load(f)

    def load_cached_data_processor(self, file_name: str) -> 'DataProcessor':
        file_data_path = Path(self.data_path, "data/transformed_data")
        return self.DataProcessorCacheManager.load(file_name=file_name, file_dir_path=file_data_path)

    def load_most_recent_transformed_data(self):
        """Load the most recent pickled data dictionary from the data/transformed directory,
        as determined by the timestamp in the filename. """

        filepath = Path(self.data_path, "data/transformed_data/*")
        files = glob.glob(filepath.__str__())

        if len(files) == 0:
            print("ERROR: no files found at {}".format(filepath))
            return

        filenames = [os.path.basename(file) for file in files]
        filenames.sort()
        chosen_one = filenames[-1]
        print("Loading {}".format(chosen_one))
        return self.load_transformed_data(chosen_one)

    def save_experiment(self, experiment_object: Any, file_name: str) -> str:
        data_interface = DataInterfaceManager.select(file_name, default_file_type='dill')
        file_dir_path = Path(self.data_path, 'experiment_objects')
        data_interface.save(experiment_object, file_name=file_name, file_dir_path=file_dir_path)
        return Path(file_dir_path, file_name)

    def load_experiment(self, file_name: str) -> Any:
        # convert the file_name to a string, because file names are experiment id numbers, so an int may be passed
        file_name = str(file_name)

        data_interface = DataInterfaceManager.select(file_name, default_file_type='dill')
        file_dir_path = Path(self.data_path, 'experiment_objects')
        experiment_object = data_interface.load(file_name=file_name, file_dir_path=file_dir_path)
        return experiment_object

    @classmethod
    def _get_git_hash(cls, path: str) -> str:
        """
        Get the short hash of latest git commit, first checking that all changes have been committed.
        If there are uncommitted changes, raise an error.
        This ensures that the git hash returned captures the true state of the code.

        Arguments:
            path (str): Path to git repo.

        Returns:
            git_hash (str): Short hash of latest commit on the active branch of the git repo.
        """
        git_hash_raw = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                               cwd=path)
        git_hash = git_hash_raw.strip().decode("utf-8")
        return git_hash

    @classmethod
    def check_for_uncommitted_git_changes(cls):
        repo_path = cls.get_repo_path()
        return cls._check_for_uncommitted_git_changes_at_path(repo_path)

    @classmethod
    def _check_for_uncommitted_git_changes_at_path(cls, repo_path: str) -> bool:
        """
        Check if there are uncommitted changes in the git repo, and raise an error if there are.

        Args:
            repo_path: str. Path to the repo to check.

        Returns: bool. False: no uncommitted changes found, Repo is valid.
            True: uncommitted changes found. Repo is not valid.
        """
        repo = Repo(repo_path, search_parent_directories=True)

        try:
            # get list of gitignore filenames and extensions as these wouldn't have been code synced over
            # and therefore would appears as if they were uncommitted changes
            with open(os.path.join(repo.working_tree_dir, '.gitignore'), 'r') as f:
                gitignore = [line.strip() for line in f.readlines() if not line.startswith('#') and line != '\n']
        except FileNotFoundError:
            gitignore = []

        gitignore_files = [item for item in gitignore if not item.startswith('*')]
        gitignore_ext = [item.strip('*') for item in gitignore if item.startswith('*')]

        # get list of changed files, but ignore ones in gitignore (either by filename match or extension match)
        changed_files = [item.a_path for item in repo.index.diff(None)
                         if os.path.basename(item.a_path) not in gitignore_files]
        changed_files = [item for item in changed_files
                         if not any([item.endswith(ext) for ext in gitignore_ext])]

        if len(changed_files) > 0:
            raise RuntimeError('There are uncommitted changes in files: {}'
                               '\nCommit them before proceeding. '.format(', '.join(changed_files)))

        return False

    @property
    def html_articles(self) -> pd.DataFrame:
        return self._articles('html')

    @property
    def clean_articles(self) -> pd.DataFrame:
        print("WARNING: this function is deprecated")
        return self._articles('cleaned_text')

    @property
    def selected_html_articles(self) -> pd.DataFrame:
        print("WARNING: this function is deprecated")
        return self._articles('remove_selected_html')

    @property
    def no_html_articles(self) -> pd.DataFrame:
        print("WARNING: this function is deprecated")
        return self._articles('remove_all_html')

    @property
    def responses(self) -> pd.DataFrame:
        if 'responses' not in self.data:
            self._load_responses()
        return self.data['responses']

    def _load_metamap_semantics(self):
        metamap_semantics_path = pkg_resources.resource_filename('autodiscern',
                                                                 'data/metamap_semantics/metamap_semantics.csv')
        self.data['metamap_semantics'] = pd.read_csv(metamap_semantics_path)

    @property
    def metamap_semantics(self) -> pd.DataFrame:
        if 'metamap_semantics' not in self.data:
            self._load_metamap_semantics()
        return self.data['metamap_semantics']


class DataProcessor:

    def __init__(self, data, processor_func, code):
        self.data_set = data
        self.processor_func = processor_func
        self.code = code

    @property
    def data(self):
        return self.data_set

    @property
    def func(self):
        return self.processor_func

    def rerun(self, *args, **kwargs):
        return self.processor_func(*args, **kwargs)

    def view_code(self):
        return self.code


class DataProcessorCacheManager:

    def __init__(self):
        self.processor_designation = '_processor'
        self.processor_data_interface = DillDataInterface
        self.code_designation = '_code'
        self.code_data_interface = TextDataInterface

    def save(self, data: Any, processing_func: Callable, file_name: str, data_file_type: str, file_dir_path: str):
        if self.check_name_already_exists(file_name, file_dir_path):
            raise ValueError("That data processor name is already in use")

        data_interface = DataInterfaceManager.select(data_file_type)
        data = processing_func(data)
        data_interface.save(data, file_name, file_dir_path)
        self.processor_data_interface.save(processing_func, file_name+self.processor_designation, file_dir_path)
        processing_func_code = inspect.getsource(processing_func)
        self.code_data_interface.save(processing_func_code, file_name+self.code_designation, file_dir_path)

    def load(self, file_name: str, file_dir_path: str) -> DataProcessor:
        """
        Load a cached data processor- the data and the function that generated it.
        Accepts a file name with or without a file extension.

        Args:
            file_name: The base name of the data file. May include the file extension, otherwise the file extension
                will be deduced.
            file_dir_path: the path to the directory where cached data processors are stored.

        Returns: Tuple(data, processing_func)

        """
        data_file_extension = None
        if '.' in file_name:
            file_name, data_file_extension = file_name.split('.')

        # load the processor
        processing_func = self.processor_data_interface.load(file_name+self.processor_designation, file_dir_path)

        # load the data
        code = self.code_data_interface.load(file_name+self.code_designation, file_dir_path)

        # find and load the data
        if data_file_extension is None:
            data_file_extension = self.get_data_processor_data_type(file_name, file_dir_path)
        data_interface = DataInterfaceManager.select(data_file_extension)
        data = data_interface.load(file_name, file_dir_path)
        return DataProcessor(data, processing_func, code)

    def check_name_already_exists(self, file_name, file_dir_path):
        existing_data_processors = self.list_cached_data_processors(file_dir_path)
        if file_name in existing_data_processors:
            return True
        else:
            return False

    @staticmethod
    def get_data_processor_data_type(file_name, file_dir_path):
        data_path = Path("{}/{}.*".format(file_dir_path, file_name))
        processor_files = glob.glob(data_path.__str__())

        if len(processor_files) == 0:
            raise ValueError("No data file found for processor {}".format(file_name))
        elif len(processor_files) > 1:
            raise ValueError("Something went wrong- there's more than one file that matches this processor name: "
                             "{}".format("\n - ".join(processor_files)))

        data_file = processor_files[0]
        data_file_extension = os.path.basename(data_file).split('.')[1]
        return data_file_extension

    def list_cached_data_processors(self, file_dir_path: str):
        # glob for all processor files
        processors_path = Path("{}/*{}.{}".format(file_dir_path, self.processor_designation,
                                                  self.processor_data_interface.file_extension))
        processor_files = glob.glob(processors_path.__str__())
        processor_names = [os.path.basename(file).split('.')[0] for file in processor_files]
        return processor_names


class DataInterface:

    file_extension = None

    @classmethod
    def construct_file_path(cls, file_name: str, file_dir_path: str) -> Path:
        """Construct the file path.
         Can handle both cases of the file extension being included in the file_name or not

        Args:
            file_name: File name, with or without file extension
            file_dir_path: Path for the file

        Returns: Path

        """
        if '.{}'.format(cls.file_extension) in file_name:
            return Path(file_dir_path, file_name)
        else:
            return Path(file_dir_path, "{}.{}".format(file_name, cls.file_extension))

    @classmethod
    def save(cls, data: Any, file_name: str, file_dir_path: str) -> None:
        file_path = cls.construct_file_path(file_name, file_dir_path)
        return cls._interface_specific_save(data, file_path)

    @classmethod
    def _interface_specific_save(cls, data: Any, file_path) -> None:
        raise NotImplementedError

    @classmethod
    def load(cls, file_name: str, file_dir_path: str) -> Any:
        file_path = cls.construct_file_path(file_name, file_dir_path)
        return cls._interface_specific_load(file_path)

    @classmethod
    def _interface_specific_load(cls, file_path) -> Any:
        raise NotImplementedError


class PickleDataInterface(DataInterface):

    file_extension = 'pkl'

    @classmethod
    def _interface_specific_save(cls, data: Any, file_path) -> None:
        with open(file_path, "wb+") as f:
            pickle.dump(data, f)

    @classmethod
    def _interface_specific_load(cls, file_path) -> Any:
        with open(file_path, "rb+") as f:
            return pickle.load(f)


class DillDataInterface(DataInterface):

    file_extension = 'dill'

    @classmethod
    def _interface_specific_save(cls, data: Any, file_path) -> None:
        with open(file_path, "wb+") as f:
            dill.dump(data, f)

    @classmethod
    def _interface_specific_load(cls, file_path) -> Any:
        with open(file_path, "rb+") as f:
            return dill.load(f)


class CSVDataInterface(DataInterface):

    file_extension = 'csv'

    @classmethod
    def _interface_specific_save(cls, data, file_path):
        data.to_csv(file_path)

    @classmethod
    def _interface_specific_load(cls, file_path):
        return pd.read_csv(file_path)


class TextDataInterface(DataInterface):

    file_extension = 'txt'

    @classmethod
    def _interface_specific_save(cls, data, file_path):
        with open(file_path, 'w+') as f:
            f.write(data)

    @classmethod
    def _interface_specific_load(cls, file_path):
        with open(file_path, 'r') as f:
            data = f.read()
        return data


class DataInterfaceManager:

    file_extension = None
    registered_interfaces = {
        'pkl': PickleDataInterface,
        'csv': CSVDataInterface,
        'dill': DillDataInterface,
        'txt': TextDataInterface,
    }

    @classmethod
    def create(cls, file_type: str) -> DataInterface:
        if file_type in cls.registered_interfaces:
            return cls.registered_interfaces[file_type]()
        else:
            raise ValueError("File type {} not recognized. Supported file types include {}".format(
                file_type, list(cls.registered_interfaces.keys())))

    @classmethod
    def select(cls, file_hint: str, default_file_type=None):
        """
        Select the appropriate data interface based on the file_hint.

        Args:
            file_hint: May be a file name with an extension, or just a file extension.
            default_file_type: default file type to use, if the file_hint doesn't specify.

        Returns: A DataInterface.

        """
        if '.' in file_hint:
            file_name, file_extension = file_hint.split('.')
            return cls.create(file_extension)
        elif file_hint in cls.registered_interfaces:
            return cls.create(file_hint)
        elif default_file_type is not None:
            return cls.create(default_file_type)
        else:
            raise ValueError("File hint {} not recognized. Supported file types include {}".format(
                file_hint, list(cls.registered_interfaces.keys())))
