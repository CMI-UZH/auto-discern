import html
import multiprocessing as mp
import re
import tldextract
from bs4 import BeautifulSoup, Comment, CData, ProcessingInstruction, Declaration, Doctype
from bs4.element import Tag
from nltk.tokenize.punkt import PunktSentenceTokenizer
from typing import Callable, Dict, List, Tuple, Set


TransformType = Callable[[str], str]


class Transformer:
    """Run a set of transforms and annotations on any input.

    Transforms are run on the string level. A transform takes a string as input, and returns a modified string.

    Segmentation is run as a special transform that takes a string and returns a List(str). Segmentation is always
        run after all other transforms.

    Annotations are run on the dict level. An annotator takes a dict with the ['content'] key set to a string, and
        returns a modified dict with additional keys. The annotation may also modify the string in ['content'].
    """

    def __init__(self, leave_some_html: bool = False, html_to_plain_text: bool = False, segment_into: str = None,
                 remove_newlines: bool = True, flatten: bool = False, annotate_html: bool = False,
                 parallelism: bool = False, num_cores=8):
        """
        Sets the parameters of the Transformer object.

        Args:
            leave_some_html: bool. Whether or not to leave some html in the text.
            html_to_plain_text: bool. Convert html tags into plain text so as not to break text segmentation.

            segment_into: str. segment the text into words, sentences, or paragraphs.
            flatten: bool. Whether to flatten the segmented texts list(dict(list)) into a single master list(dict).

            annotate_html: bool. Set annotations on the dict about the presence of html tags. Also removes html tags.

            parallelism: bool. Whether to run the transforms in parallel. Not compatible with sentence segmentation.
            num_cores: int. Number of cores to use when using multiprocessing.

        Returns: None

        """
        self.transforms = []
        self.annotations = []
        self.parallelism = parallelism
        self.num_cores = num_cores
        self.flatten = flatten

        if leave_some_html and segment_into is not None and html_to_plain_text is False:
            print("WARNING: segmentation does not work well with html remaining in the sentence. Consider setting "
                  "html_to_plain_text to True. ")

        if flatten and segment_into is None:
            print("WARNING: Flattening should only be applied when segmentation is also applied. Make sur you know "
                  "what you're doing! ")

        # text cleaning transform to use
        if leave_some_html:
            if html_to_plain_text:
                self.transforms.append(self._to_limited_html_plain_text)
            else:
                self.transforms.append(self._to_limited_html)
        else:
            self.transforms.append(self._to_text)

        # segmentation transform to use
        if segment_into is None:
            pass
        elif segment_into in {'w', 'word', 'words'}:
            from allennlp.data.tokenizers.word_tokenizer import WordTokenizer
            self.transforms.append(self._to_words)
            self.segmentation_type = 'words'
            self.segmenter_helper_obj = WordTokenizer()
        elif segment_into in {'s', 'sent', 'sents', 'sentence', 'sentences'}:
            self.transforms.append(self._to_sentences)
            self.segmentation_type = 'sentences'
            # nlp = spacy.load('en', disable=['ner'])
            # self.segmenter_helper_obj = nlp
            self.segmenter_helper_obj = PunktSentenceTokenizer()
        elif segment_into in {'p', 'para', 'paragraph', 'paragraphs'}:
            self.transforms.append(self._to_paragraphs)
            self.segmentation_type = 'paragraphs'
            self.segmenter_helper_obj = None
        else:
            raise ValueError("Invalid segment_type: {}".format(segment_into))

        if remove_newlines:
            self.transforms.append(self.remove_newlines)
            self.transforms.append(self.regex_out_punctuation_and_white_space)

        # annotations to use
        if annotate_html:
            self.annotations.append(self._annotate_and_clean_html)
            self.annotations.append(self._annotate_internal_external_links)

    def _apply_transforms_to_str(self, content: str) -> str:
        """Apply list all transforms to str. """
        for f in self.transforms:
            content = f(content)
        return content

    def _transform_worker(self, obj: Dict) -> Dict:
        """Create a new transformed object. """
        transformed_obj = {key: obj[key] for key in obj if key != 'content'}
        transformed_obj['content'] = self._apply_transforms_to_str(obj['content'])
        return transformed_obj

    def _apply_annotations_to_dict(self, content: Dict) -> Dict:
        """Apply list all annotations to dict. """

        for f in self.annotations:
            content = f(content)
        return content

    def _annotation_worker(self, obj: Dict) -> Dict:
        """Create a new transformed object. """
        return self._apply_annotations_to_dict(obj)

    def apply_in_parallel(self, input_list: List[Dict], worker: Callable) -> List:
        """Run all transforms on input_list in parallel. """
        pool = mp.Pool(self.num_cores)
        results = pool.map(worker, (i for i in input_list))
        pool.close()
        pool.join()
        return results

    def apply_in_series(self, input_list: List[Dict], worker: Callable) -> List:
        """Run all transforms on input_list in series. """
        results = []
        for i in input_list:
            results.append(worker(i))
        return results

    def _apply_transforms(self, input_list: List[Dict]) -> List:
        if self.parallelism:
            result = self.apply_in_parallel(input_list, worker=self._transform_worker)
        else:
            result = self.apply_in_series(input_list, worker=self._transform_worker)
        return result

    def _apply_annotations(self, input_list: List[Dict]) -> List:
        if self.parallelism:
            result = self.apply_in_parallel(input_list, worker=self._annotation_worker)
        else:
            result = self.apply_in_series(input_list, worker=self._annotation_worker)
        return result

    def apply(self, inputs: Dict[int, Dict]) -> Dict[int, Dict]:
        """Run all transforms and annotations on input_list.
        Converts a Dict of {entity_id_int: data_dict} to a list of [data_dict] for processing, and then back to dict
        of dicts on return.
        """

        input_list = [inputs[id] for id in inputs]

        # run transformations, including segmentation, at the string level
        result = self._apply_transforms(input_list)

        # if segmentation occurred, flatten list of dicts with now lists in 'content' into one single list of dicts
        if self.flatten:
            result = self._flatten_text_dicts(result)

        # run annotations, at the dict level
        result = self._apply_annotations(result)

        back_to_dict = convert_list_of_dicts_to_dict_of_dicts(result)
        return back_to_dict

    # === High Level Transformer functions =======================

    def _to_limited_html(self, x: str) -> str:
        soup = BeautifulSoup(x, features="html.parser")
        soup = self.remove_tags_and_contents(soup, ['style', 'script'])
        soup = self.remove_other_xml(soup)
        soup = self.reformat_html_link_tags(soup)

        tags_to_keep = {'h1', 'h2', 'h3', 'h4'}
        tags_to_keep_with_attr = {'a'}
        tags_to_replace = {
            'br': ('.\n', '.\n'),
            'p': ('\n', '\n'),
        }
        default_tag_replacement_str = ''
        text = self.replace_html(soup, tags_to_keep, tags_to_keep_with_attr, tags_to_replace,
                                 default_tag_replacement_str)

        text = self.replace_chars(text, ['\t', '\xa0'], ' ')
        text = self.regex_out_punctuation_and_white_space(text)
        text = self.condense_line_breaks(text)

        return text

    def _to_limited_html_plain_text(self, x: str) -> str:
        clean_x = self.clear_non_rendered_html(x)
        soup = BeautifulSoup(clean_x, features="html.parser")
        soup = self.remove_tags_and_contents(soup, ['style', 'script'])
        soup = self.remove_other_xml(soup)

        tags_to_keep = set()
        tags_to_keep_with_attr = set()
        tags_to_replace = {
            'br': ('.\n', '.\n'),
            'h1': (' thisisah1tag ', '. \n'),
            'h2': (' thisisah2tag ', '. \n'),
            'h3': (' thisisah3tag ', '. \n'),
            'h4': (' thisisah4tag ', '. \n'),
            'a':  (' thisisalinktag ', ' '),
            'li': ('\n thisisalistitemtag ', '. \n'),
            'tr': ('\n thisisatablerowtag ', '. \n'),
            'p':  ('\n', '. \n'),
            'div': ('. \n', '. \n'),
        }
        default_tag_replacement_str = ''
        text = self.replace_html(soup, tags_to_keep, tags_to_keep_with_attr, tags_to_replace,
                                 default_tag_replacement_str, include_link_domains=True)

        text = self.replace_chars(text, ['\t', '\xa0'], ' ')
        text = self.regex_out_punctuation_and_white_space(text)
        text = self.condense_line_breaks(text)

        return text

    def _to_text(self, x: str) -> str:
        clean_x = self.clear_non_rendered_html(x)
        soup = BeautifulSoup(clean_x, features="html.parser")
        soup = self.remove_tags_and_contents(soup, ['style', 'script'])
        soup = self.remove_other_xml(soup)

        tags_to_keep = set()
        tags_to_keep_with_attr = set()
        tags_to_replace = {
            'br': ('.\n', '.\n'),
            'h1': ('\n', '. \n'),
            'h2': ('\n', '. \n'),
            'h3': ('\n', '. \n'),
            'h4': ('\n', '. \n'),
            'p':  ('\n', '. \n'),
            'div': ('\n', '. \n'),
        }
        default_tag_replacement_str = ''
        text = self.replace_html(soup, tags_to_keep, tags_to_keep_with_attr, tags_to_replace,
                                 default_tag_replacement_str)

        text = self.replace_chars(text, ['\t', '\xa0'], ' ')
        text = self.regex_out_punctuation_and_white_space(text)
        text = self.condense_line_breaks(text)

        return text

    # === High Level Segmentation functions ======================

    def _to_words(self, x: str) -> List[str]:
        tok = self.segmenter_helper_obj
        return [str(t) for t in tok.tokenize(x)]

    def _to_sentences(self, x: str) -> List[str]:
        # spacy sentence tokenizer version
        # nlp = self.segmenter_helper_obj
        # doc = nlp(x)
        # result = [sent.string.strip() for sent in doc.sents]
        tokenizer = self.segmenter_helper_obj
        result = [sent.strip() for sent in tokenizer.tokenize(x)]
        return result

    def _to_paragraphs(self, x: str) -> List[str]:
        x = self.condense_line_breaks(x)
        return x.split('\n')

    @staticmethod
    def _flatten_text_dicts(list_of_dicts: List[Dict]) -> List[Dict]:
        output = []
        for parent_dict in list_of_dicts:
            for num, i in enumerate(parent_dict['content']):
                child_dict = {key: parent_dict[key] for key in parent_dict if key != 'content'}
                child_dict['content'] = i
                child_dict['sub_id'] = num
                output.append(child_dict)
        return output

    # === BeautifulSoup Helper functions =========================
    @staticmethod
    def remove_tags_and_contents(soup: BeautifulSoup, tags: List[str]) -> BeautifulSoup:
        """Remove specific tags from the html, including their entire contents."""
        for tag in soup.find_all(True):
            if tag.name in tags:
                # delete tag and its contents
                tag.decompose()
        return soup

    @staticmethod
    def remove_other_xml(soup: BeautifulSoup) -> BeautifulSoup:
        for tag in soup.find_all(string=lambda text: isinstance(text, Comment)
                                 or isinstance(text, CData)
                                 or isinstance(text, ProcessingInstruction)
                                 or isinstance(text, Declaration)
                                 or isinstance(text, Doctype)
                                 ):
            tag.extract()
        return soup

    @staticmethod
    def reformat_html_link_tags(soup: BeautifulSoup) -> BeautifulSoup:
        """
        Reformat html link tags to have no attributes other than the href or src.
        Set the href/src to just the domain name of the link.

        Note: we want drugs.rcpsych.co.uk and depression.rcpsych.co.uk to both resolve to rcpsych.

        Args:
            soup: BeautifulSoup object parsing an html

        Returns: BeautifulSoup

        """
        for tag in soup.find_all(True):
            if tag.name == 'a':
                attrs = dict(tag.attrs)
                for attr in attrs:
                    if attr in ['src', 'href']:
                        url = tag.attrs[attr]
                        domain = tldextract.extract(url).domain
                        tag.attrs[attr] = domain
                    else:
                        del tag.attrs[attr]
        return soup

    @staticmethod
    def get_domain_from_link_tag(tag: Tag) -> str:
        """Extract the domain from a url. If no domain is found, assume link is a filepath, and return NA"""
        attrs = dict(tag.attrs)
        for attr in attrs:
            if attr in ['src', 'href']:
                url = tag.attrs[attr]
                domain = tldextract.extract(url).domain
                if domain != '':
                    return domain
                else:
                    return 'NA'
        return 'NA'

    def replace_html(self, soup: BeautifulSoup, tags_to_keep: Set[str], tags_to_keep_with_attr: Set[str],
                     tags_to_replace_with_str: Dict[str, Tuple[str, str]], default_tag_replacement_str: str,
                     include_link_domains=False) -> str:
        """
        Finds all tags in an html BeautifulSoup object and replaces/keeps the tags in accordance with args.

        Args:
            soup: BeautifulSoup object parsing an html
            tags_to_keep: html tags to leave but remove tag attributes
            tags_to_keep_with_attr: html tags to leave intact
            tags_to_replace_with_str: html tags to replace with strings defined in replacement Tuple(start_tag, end_tag)
            default_tag_replacement_str: string to use if no replacement is defined in tags_to_replace_with_str
            include_link_domains: bool. Append the domain of the linked url to the replacement tag

        Returns: str

        """

        all_tags = set([tag.name for tag in soup.find_all()])
        tags_to_replace = all_tags - tags_to_keep - tags_to_keep_with_attr
        tags_to_replace = tags_to_replace | set(tags_to_replace_with_str.keys())

        default_replacement_tuple = (default_tag_replacement_str, default_tag_replacement_str)

        for tag in soup.find_all(True):
            if tag.name in tags_to_keep_with_attr:
                # keep tag, including attributes
                pass
            elif tag.name in tags_to_keep:
                # keep tag but clear all attributes
                tag.attrs = {}
            elif tag.name in tags_to_replace:
                # if tag replacement is not specified, use the default
                r = tags_to_replace_with_str.get(tag.name, default_replacement_tuple)
                start_tag_replacement = r[0]
                end_tag_replacement = r[1]

                if tag.name == 'a' and include_link_domains:
                    domain = self.get_domain_from_link_tag(tag)
                    start_tag_replacement = start_tag_replacement.rstrip() + domain + ' '
                tag.insert_before(start_tag_replacement)
                tag.insert_after(end_tag_replacement)
                # remove the tag without removing the tag's contents (text and children tags)
                tag.unwrap()

        text = self.soup_to_text_with_tags(soup)
        return text

    @staticmethod
    def soup_to_text_with_tags(soup: BeautifulSoup) -> str:
        """Convert a BeautifulSoup object to a string while leaving the html tags in place."""
        text = str(soup)
        text = html.unescape(text)
        return text

    # === String-Based Helper functions ==========================

    @staticmethod
    def clear_non_rendered_html(text: str) -> str:
        return text.replace('\n', ' ')

    @staticmethod
    def regex_out_punctuation_and_white_space(text: str) -> str:
        """Clean up excess whitespace and punctuation."""
        text = text.replace('?.', '?')

        # replaces multiple spaces wth a single space
        text = re.sub(r' +', ' ',  text)
        # replace occurences of '.' followed by any combination of '.', ' ', or '\n' with single '.'
        #  for handling html -> '.' replacement.
        text = re.sub(r"[.][. ]{2,}", '. ', text)
        text = re.sub(r"[?][. ]{2,}", '? ', text)
        text = re.sub(r"[!][. ]{2,}", '! ', text)
        text = re.sub(r"[.][. \n]{2,}", '. \n', text)
        text = re.sub(r"[?][. \n]{2,}", '? \n', text)
        text = re.sub(r"[!][. \n]{2,}", '! \n', text)

        # if there is a period at the very start of the document, remove it (replace 1 time)
        text = text.lstrip()
        if len(text) > 0 and text[0] == '.':
            text = text.replace('.', '', 1).lstrip()

        return text

    @staticmethod
    def condense_line_breaks(text: str) -> str:
        # replaces multiple spaces wth a single space
        text = re.sub(r' +', ' ',  text).strip()

        # replace html line breaks with new line characters
        text = re.sub(r'<br[/]*>', '\n', text)

        # replace any combination of ' ' and '\n' with single ' \n'
        text = re.sub(r"[ \n]{2,}", ' \n', text)
        return text

    @staticmethod
    def replace_chars(x: str, chars_to_replace: List[str], replacement_char: str) -> str:
        """Replace all chars_to_replace with replacement_char. """
        for p in chars_to_replace:
            x = x.replace(p, replacement_char)
        return x

    @classmethod
    def remove_newlines(cls, x: str) -> str:
        return cls.replace_chars(x, ['\n'], ' ')

    # === Annotation Functions ==================================

    @staticmethod
    def _retrieve_domain_from_plaintexttag(token: str) -> (str, str):
        plaintexttag = "thisisalinktag"
        tag_pos = token.find(plaintexttag)
        tag_and_domain = token[tag_pos:]
        domain = tag_and_domain.replace(plaintexttag, '')
        cleaned_token = token.replace(tag_and_domain, '')
        return cleaned_token, domain

    @classmethod
    def _annotate_and_clean_html(cls, d: Dict, extract_domains=True) -> Dict:
        tags = {
            'thisisah1tag': 'h1',
            'thisisah2tag': 'h2',
            'thisisah3tag': 'h3',
            'thisisah4tag': 'h4',
            'thisisalinktag': 'a',
            'thisisalistitemtag': 'li',
            'thisisatablerowtag': 'tr',
        }
        found_tags = []
        domains = []
        for plaintexttag in tags:
            if plaintexttag in d['content']:
                if plaintexttag == 'thisisalinktag' and extract_domains:
                    # TODO: this space splitting is not robust
                    tokens = d['content'].split(' ')
                    text_without_tags = ''
                    for i, token in enumerate(tokens):
                        if plaintexttag in token:
                            cleaned_token, domain = cls._retrieve_domain_from_plaintexttag(token)
                            domains.append(domain)
                            if cleaned_token != '':
                                text_without_tags += cleaned_token + ' '
                        else:
                            if token != '':
                                text_without_tags += token + ' '
                    d['content'] = text_without_tags.rstrip()
                    found_tags.append(tags[plaintexttag])
                else:
                    d['content'] = d['content'].replace(plaintexttag, ' ').strip()
                    found_tags.append(tags[plaintexttag])
        d['html_tags'] = found_tags
        d['domains'] = domains
        return d

    @staticmethod
    def _annotate_internal_external_links(d: Dict) -> Dict:
        if 'url' not in d.keys():
            print("WARNING: text url is not available for linked domain comparison")
            return d
        source_domain = tldextract.extract(d['url']).domain
        d['link_type'] = []
        for link in d['domains']:
            if link == 'NA':
                # assume links that are not valid urls are internal filepaths
                d['link_type'].append('internal')
            elif link == source_domain:
                d['link_type'].append('internal')
            else:
                d['link_type'].append('external')
        return d


# ============================================================
# === Other non-class functions ==============================
# ============================================================

def get_id(d: Dict) -> str:
    id_key = 'id'
    if id_key not in d.keys() and 'entity_id' in d.keys():
        id_key = 'entity_id'
    identifier = d[id_key]
    if 'sub_id' in d:
        identifier = "{}-{}".format(d[id_key], d['sub_id'])
    return identifier


def convert_list_of_dicts_to_dict_of_dicts(input_list: List[Dict]) -> Dict[str, Dict]:
    output_dict = {}
    for d in input_list:
        id = get_id(d)
        output_dict[id] = d
    return output_dict
