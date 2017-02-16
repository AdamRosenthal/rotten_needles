"""Crawls and extracts choice metrics from IMDB movie profiles."""

import re
import os
from datetime import datetime
from urllib import request
import urllib
import decimal

from bs4 import BeautifulSoup as bs
import click
from tqdm import tqdm
import pandas as pd

from .jsondate import load, dump


# === utility methods ===

def clear_empty_profiles():
    """Clears all empty profiles in the profile directory."""
    print("Clearing empty movie profiles...")
    if not os.path.exists(PROFILES_DIR_PATH):
        print("No profiles to clear!")
    for profile_file in os.listdir(PROFILES_DIR_PATH):
        file_path = os.path.join(PROFILES_DIR_PATH, profile_file)
        with open(file_path, 'r') as json_file:
            if sum(1 for line in json_file) < 1:
                print('Deleting {}'.format(profile_file))
                # os.remove(file_path)


def _parse_string(string):
    return string.lower().strip().replace(' ', '_')


# ==== extracting movie properties ====

def _get_rating(prof_page):
    return float(prof_page.find_all(
        "span", {"itemprop": "ratingValue"})[0].contents[0])


def _get_rating_count(prof_page):
    return int(prof_page.find_all(
        "span", {"itemprop": "ratingCount"})[0].contents[0].replace(',', ''))


def _get_geners(prof_page):
    genres = []
    for span in prof_page.find_all("span", {"itemprop": "genre"}):
        genres.append(_parse_string(span.contents[0]))
    return genres


REVIEW_COUNT_REGEX = r'([0-9,]+) ([a-zA-Z]+)'

def _get_review_counts(prof_page):
    user_review_count = 0
    critic_review_count = 0
    for span in prof_page.find_all("span", {"itemprop": "reviewCount"}):
        span_str = span.contents[0]
        res = re.findall(REVIEW_COUNT_REGEX, span_str)[0]
        if res[1] == 'user':
            user_review_count = int(res[0].replace(',', ''))
        elif res[1] == 'critic':
            critic_review_count = int(res[0].replace(',', ''))
    return user_review_count, critic_review_count


def _get_metascore(prof_page):
    try:
        return int(prof_page.find_all(
            "div", {"class": "metacriticScore"})[0].contents[1].contents[0])
    except IndexError:
        return None


def _get_year(prof_page):
    return int(prof_page.find_all(
        "span", {"id": "titleYear"})[0].contents[1].contents[0])


MOVIE_DURATION_REGEX = r'PT([0-9]+)M'

def _get_duration(prof_page):
    duration_str = prof_page.find_all(
        "time", {"itemprop": "duration"})[0]['datetime']
    return int(re.findall(MOVIE_DURATION_REGEX, duration_str)[0])


# ==== crawling the box office section ====

BUDGET_REGEX = r"<h4.*>Budget:</h4>\s*[\$\£]([0-9,]+)"

def _get_budget(box_contents):
    try:
        return int(re.findall(BUDGET_REGEX, box_contents)[0].replace(',', ''))
    except IndexError:
        return None


BUDGET_CURRENCY_REGEX = r"<h4.*>Budget:</h4>\s*([\$\£])"

def _get_budget_currency(box_contents):
    try:
        return re.findall(BUDGET_CURRENCY_REGEX, box_contents)[0]
    except IndexError:
        return None


OPEN_DATE_REGEX = r"<h4.*>Opening Weekend:</h4>[\s\S]*?\([A-Z]+\)[\s\S]*?" \
                  r"\(([0-9a-zA-Z\s]+)\)[\s\S]*?<h4"

def _get_opening_weekend_date(box_contents):
    try:
        open_date_str = re.findall(OPEN_DATE_REGEX, box_contents)[0]
        return datetime.strptime(open_date_str, "%d %B %Y").date()
    except IndexError:
        return None


OPEN_INC_REGEX = r"<h4.*>Opening Weekend:</h4>\s*[\$\£]([0-9,]+)"

def _get_opening_weekend_income(box_contents):
    try:
        return int(re.findall(
            OPEN_INC_REGEX, box_contents)[0].replace(',', ''))
    except IndexError:
        return None


OPEN_INC_CURRENCY_REGEX = r"<h4.*>Opening Weekend:</h4>\s*([\$\£])[0-9,]+"

def _get_opening_weekend_income_currency(box_contents):
    try:
        return re.findall(OPEN_INC_CURRENCY_REGEX, box_contents)[0]
    except IndexError:
        return None


CLOSING_DATE_REGEX = r"<h4.*>Gross:</h4>[\s\S]*?\([A-Z]+\)[\s\S]*?" \
                     r"\(([0-9a-zA-Z\s]+)\)"

def _get_closing_date(box_contents):
    try:
        gross_date_str = re.findall(CLOSING_DATE_REGEX, box_contents)[0]
        return datetime.strptime(gross_date_str, "%d %B %Y").date()
    except IndexError:
        return None


GROSS_REGEX = r"<h4.*>Gross:</h4>\s*\$([0-9,]+)[\s\S]*?\([A-Z]+\)"

def _get_gross_income(box_contents):
    try:
        return int(re.findall(GROSS_REGEX, box_contents)[0].replace(',', ''))
    except IndexError:
        return None


BOX_CONTENT_REGEX = r"<h3.*>Box Office</h3>([\s\S]+?)<h3"

def _get_box_office_props(prof_page):
    box_contents = re.findall(BOX_CONTENT_REGEX, str(prof_page))[0]
    box_props = {}
    box_props['budget'] = _get_budget(box_contents)
    box_props['budget_currency'] = _get_budget_currency(box_contents)
    box_props['opening_weekend_date'] = _get_opening_weekend_date(box_contents)
    box_props['opening_weekend_income'] = _get_opening_weekend_income(
        box_contents)
    box_props['opening_weekend_income_currency'] = \
        _get_opening_weekend_income_currency(box_contents)
    box_props['closing_date'] = _get_closing_date(box_contents)
    box_props['gross_income'] = _get_gross_income(box_contents)
    return box_props


# ==== crawling the ratings page ====

def _extract_table(table):
    content = []
    for row in table.find_all("tr")[1:]:
        content.append([td.get_text() for td in row.find_all("td")])
    return content


RATINGS_URL = 'http://www.imdb.com/title/{code}/ratings'

def _get_rating_props(movie_code):
    cur_ratings_url = RATINGS_URL.format(code=movie_code)
    ratings_page = bs(request.urlopen(cur_ratings_url), "html.parser")
    tables = ratings_page.find_all("table")
    hist_table = tables[0]
    hist_content = _extract_table(hist_table)
    rating_freq = {}
    for row in hist_content:
        rating_freq[int(row[2])] = int(row[0])
    rating_props = {}
    rating_props['rating_freq'] = rating_freq
    demog_table = tables[1]
    demog_content = _extract_table(demog_table)
    votes_per_demo = {}
    avg_rating_per_demo = {}
    for row in demog_content:
        try:
            votes_per_demo[_parse_string(row[0])] = int(row[1])
            avg_rating_per_demo[_parse_string(row[0])] = float(row[2])
        except IndexError:
            pass
    rating_props['votes_per_demo'] = votes_per_demo
    rating_props['avg_rating_per_demo'] = avg_rating_per_demo
    return rating_props


# ==== crawling a movie profile ====

TITLE_QUERY = (
    'http://www.imdb.com/find'
    '?q={title}&s=tt&ttype=ft&exact=true&ref_=fn_tt_ex'
)
MOVIE_CODE_REGEX = r'/title/([a-z0-9]+)/'
PROFILE_URL = 'http://www.imdb.com/title/{code}/' #?region=us


def _convert_title(title):
    return urllib.parse.quote(title).lower()


def crawl_movie_profile(movie_name):
    """Returns a basic profile for the given movie."""

    # Search
    query = TITLE_QUERY.format(title=_convert_title(movie_name))
    search_res = bs(request.urlopen(query), "html.parser")
    tables = search_res.find_all("table", {"class": "findList"})
    if len(tables) < 1:
        return {}
    res_table = tables[0]
    first_row = res_table.find_all("tr")[0]
    movie_code = re.findall(MOVIE_CODE_REGEX, str(first_row))[0]

    # Movie Profile
    cur_profile_url = PROFILE_URL.format(code=movie_code)
    prof_page = bs(request.urlopen(cur_profile_url), "html.parser")

    # Extracting properties
    props = {}
    props['name'] = movie_name
    props['rating'] = _get_rating(prof_page)
    props['rating_count'] = _get_rating_count(prof_page)
    props['genres'] = _get_geners(prof_page)
    props['user_review_count'], props['critic_review_count'] = \
        _get_review_counts(prof_page)
    props['metascore'] = _get_metascore(prof_page)
    props['year'] = _get_year(prof_page)
    props['duration'] = _get_duration(prof_page)
    props.update(_get_box_office_props(prof_page))
    props.update(_get_rating_props(movie_code))
    return props


# ==== interface ====

HOMEDIR = os.path.expanduser("~")
IMDB_SUBPACKAGE_PATH = os.path.dirname(os.path.realpath(__file__))
PACKAGE_DIR_PATH = os.path.dirname(IMDB_SUBPACKAGE_PATH)
REPO_DIR_PATH = os.path.dirname(PACKAGE_DIR_PATH)
DATA_DIR_PATH = os.path.join(REPO_DIR_PATH, 'data')
PROFILES_DIR_PATH = os.path.join(DATA_DIR_PATH, 'movie_profiles')


def save_movie_profile(movie_name, verbose, parent_pbar=None):
    """Extracts a movie profile from IMDB and saves it to disk."""
    def _print(msg):
        if verbose:
            if parent_pbar is not None:
                parent_pbar.set_description(msg)
            else:
                print(msg)

    if not os.path.exists(PROFILES_DIR_PATH):
        os.makedirs(PROFILES_DIR_PATH)
    file_name = movie_name.replace(' ', '_').lower() + '.json'
    file_path = os.path.join(PROFILES_DIR_PATH, file_name)
    if os.path.isfile(file_path):
        _print('Movie already processed.')
        return

    _print("Extracting a profile for {} from IMDB...".format(movie_name))
    props = crawl_movie_profile(movie_name)
    _print("Profile extracted succesfully")

    _print("Saving profile for {} to disk...".format(movie_name))
    with open(file_path, 'w+') as json_file:
        # json.dump(props, json_file, cls=_RottenJsonEncoder, indent=2)
        dump(props, json_file, indent=2)
    _print("Done saving a profile for {}.".format(movie_name))


@click.command()
@click.argument("movie_name", type=str, nargs=1)
@click.option("-v", "--verbose", is_flag=True,
              help="Print information to screen.")
def save_cli(movie_name, verbose):
    """Extracts a movie profile from IMDB and saves it to disk."""
    save_movie_profile(movie_name, verbose)


@click.command()
@click.argument("file_path", type=str, nargs=1)
@click.option("-v", "--verbose", is_flag=True,
              help="Print information to screen.")
def crawl_by_file(file_path, verbose=False):
    """Crawls IMDB and builds movie profiles for a movies in the given file."""
    with open(file_path, 'r') as movies_file:
        if verbose:
            print("Crawling over all movies in {}...".format(file_path))
        movie_pbar = tqdm(movies_file)
        for line in movie_pbar:
            save_movie_profile(line.strip(), verbose, movie_pbar)


# === uniting movie profiles to csv ===

DEMOGRAPHICS = [
    'aged_under_18',
    'males_under_18',
    'males_aged_45+',
    'females',
    'males_aged_18-29',
    'imdb_staff',
    'imdb_users',
    'males',
    'aged_30-44',
    'females_aged_45+',
    'aged_18-29',
    'females_aged_18-29',
    'aged_45+',
    'males_aged_30-44',
    'top_1000_voters',
    'females_under_18',
    'females_aged_30-44',
    'us_users',
    'non-us_users'
]

def _decompose_dict_column(df, colname, allowed_cols):
    newdf = df[colname].apply(pd.Series)
    newdf = newdf.drop([
        col for col in newdf.columns if col not in allowed_cols], axis=1)
    newdf.columns = [colname+'.'+col for col in newdf.columns]
    return pd.concat([df.drop([colname], axis=1), newdf], axis=1)


def _dummy_list_column(df, colname):
    value_set = set([
        value for value_list in df[colname].dropna() for value in value_list])
    def _value_list_to_dict(value_list):
        try:
            return {
                value : 1 if value in value_list else 0
                for value in value_set}
        except TypeError:
            return {value : 0 for value in value_set}
    df[colname] = df[colname].apply(_value_list_to_dict)
    return _decompose_dict_column(df, colname, list(value_set))


def unite_profiles():
    """Unite all movie profiles in the profile directory."""
    print("Uniting movie profiles unti one csv file...")
    if not os.path.exists(PROFILES_DIR_PATH):
        print("No profiles to unite!")
    profiles = []
    for profile_file in os.listdir(PROFILES_DIR_PATH):
        print('Reading {}'.format(profile_file))
        file_path = os.path.join(PROFILES_DIR_PATH, profile_file)
        file_name, ext = os.path.splitext(file_path)
        if ext == '.json':
            with open(file_path, 'r') as json_file:
                profiles.append(load(json_file))
    df = pd.DataFrame(profiles)
    df = _decompose_dict_column(df, 'avg_rating_per_demo', DEMOGRAPHICS)
    df = _decompose_dict_column(df, 'votes_per_demo', DEMOGRAPHICS)
    df = _decompose_dict_column(
        df, 'rating_freq', [str(i) for i in range(1, 11)])
    df = _dummy_list_column(df, 'genres')
    unison_fpath = os.path.join(DATA_DIR_PATH, 'movie_profiles.csv')
    df.to_csv(unison_fpath)
