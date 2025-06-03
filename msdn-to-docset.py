#!/usr/bin/env python3

import argparse
import collections
import glob
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib
import urllib.parse
import zipfile

import requests
from bs4 import BeautifulSoup as bs  # pip install bs4
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.DEBUG)


# from selenium.webdriver import Firefox
# from selenium.webdriver.firefox.options import Options
# from selenium.webdriver.firefox.firefox_binary import FirefoxBinary


class PoshWebDriver:
    """ Thin wrapper for selenium webdriver for page content retrieval """

    def __init__(self, executable_path=None):

        self.options = Options()
        self.options.add_argument("--headless")
        self.options.add_argument("--window-size=1920x1080")

        self.driver = webdriver.Chrome(options=self.options)

    def get_url_page(self, url):
        """ retrieve the full html content of a page after Javascript execution """

        index_html = None
        try:
            self.driver.get(url)
            index_html = self.driver.page_source
        except (ConnectionResetError, urllib.error.URLError) as e:
            # we may have a triggered a anti-scraping time ban
            # Lay low for several seconds and get back to it.

            self.driver.quit()
            time.sleep(5)

            self.driver = webdriver.Chrome(options=self.options)

            index_html = None

        # try a second time, and raise error if fail
        if not index_html:
            self.driver.get(url)
            index_html = self.driver.page_source

        return index_html

    def quit(self):
        return self.driver.quit()


class Configuration:
    # STATIC CONSTANTS
    docset_name = 'MSDN'

    domain = "docs.microsoft.com"
    default_theme_uri = "_themes/docs.theme/master/en-us/_themes"

    def __init__(self, args):
        # # selected powershell api version
        # self.powershell_version = args.version

        # # The modules and cmdlets pages are "versionned" using additional params in the GET request
        # self.powershell_version_param = "view=powershell-{0:s}".format(self.powershell_version)

        # build folder (must be cleaned afterwards)
        # self.build_folder = os.path.join(os.getcwd(), "_build_{0:s}".format(self.powershell_version))
        self.build_folder = os.path.join(os.getcwd(), "_build_msdn")

        # output file
        self.output_filepath = os.path.realpath(args.output)

        # powershell docs start page
        self.api_index_url = "https://docs.microsoft.com/en-us/windows/win32/api/"

        self.docs_index_url = "https://docs.microsoft.com/en-us/windows/win32/desktop-app-technologies"

        # # powershell docs table of contents url
        # self.docs_toc_url =  "https://{0:s}/psdocs/toc.json?{2:s}".format(
        #     Configuration.base_url, 
        #     self.powershell_version,
        #     self.powershell_version_param
        # )

        # self.windows_toc_url = "https://{0:s}/windowsserver2019-ps/toc.json?view=windowsserver2019-ps".format(
        #     Configuration.base_url
        # )

        # selenium webdriver
        self.webdriver = PoshWebDriver()

        self.crawl_contents = True

        # selected module
        # self.filter_modules = [module.lower() for module in args.modules]


# Global session for several retries
session = requests.Session()
retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries))


def download_binary(url, output_filename):
    """ Download GET request as binary file """
    global session

    logger.debug("download_binary : %s -> %s" % (url, output_filename))

    # ensure the folder path actually exist
    os.makedirs(os.path.dirname(output_filename), exist_ok=True)

    r = session.get(url, stream=True)
    with open(output_filename, 'wb') as f:
        for data in r.iter_content(32 * 1024):
            f.write(data)


def download_textfile(url: str, output_filename: str, params: dict = None):
    """ Download GET request as utf-8 text file """
    global session

    logger.debug("download_textfile : %s -> %s" % (url, output_filename))
    # ensure the folder path actually exist
    os.makedirs(os.path.dirname(output_filename), exist_ok=True)

    while True:
        try:
            r = session.get(url, data=params)
        except ConnectionError:
            logger.debug("caught ConnectionError, retrying...")
            time.sleep(2)
        else:
            break

    # do not write 404 pages on disk
    if r.status_code != 200:
        return False

    r.encoding = 'utf-8'
    with open(output_filename, 'w', encoding="utf-8") as f:
        f.write(r.text)

    return True


def make_docset(source_dir, dst_filepath, filename):
    """ 
    Tar-gz the build directory while conserving the relative folder tree paths. 
    Copied from : https://stackoverflow.com/a/17081026/1741450 
    """
    dst_dir = os.path.dirname(dst_filepath)
    tar_filepath = os.path.join(dst_dir, '%s.tar' % filename)

    with tarfile.open(tar_filepath, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))

    shutil.move(tar_filepath, dst_filepath)


def download_page_contents(configuration, uri, output_filepath):
    """ Download a page using it's uri from the TOC """

    # Resolving "absolute" url et use appropriate version
    full_url = urllib.parse.urljoin(configuration.docs_toc_url, uri)
    versionned_url = "{0:s}?{1:s}".format(full_url, configuration.powershell_version_param)

    download_textfile(versionned_url, output_filepath)


def download_module_contents(configuration, module_name, module_uri, module_dir, cmdlets, root_dir):
    """ Download a modules contents """

    module_filepath = os.path.join(module_dir, "%s.html" % module_name)

    logger.debug("downloading %s module index page  -> %s" % (module_name, module_filepath))
    if module_uri:
        download_page_contents(configuration, module_uri, module_filepath)

    cmdlets_infos = []

    # Downloading cmdlet contents
    for cmdlet in cmdlets:

        cmdlet_name = cmdlet['toc_title']
        if cmdlet_name.lower() in ("about", "functions", "providers", "provider"):  # skip special toc
            continue

        cmdlet_uri = cmdlet["href"]
        cmdlet_filepath = os.path.join(module_dir, "%s.html" % cmdlet_name)

        logger.debug("downloading %s cmdlet doc -> %s" % (cmdlet_name, cmdlet_filepath))
        download_page_contents(configuration, cmdlet_uri, cmdlet_filepath)

        cmdlets_infos.append(
            {
                'name': cmdlet_name,
                'path': os.path.relpath(cmdlet_filepath, root_dir),
            }
        )

    module_infos = {
        'name': module_name,
        'index': os.path.relpath(module_filepath, root_dir),
        'cmdlets': cmdlets_infos
    }

    return module_infos


def _findname(obj, key):
    """ return the 'toc_title' value associated to a 'href' node """
    # print("%r == %s" % (obj.get('href', None), key))
    if obj.get('href', None) == key: return obj['toc_title']
    for k, v in obj.items():
        if isinstance(v, dict):
            item = _findname(v, key)
            if item is not None:
                return item
        if isinstance(v, list):
            for i in v:
                item = _findname(i, key)
                if item is not None:
                    return item


def crawl_sdk_api_folder(
        configuration: Configuration,
        download_dir: str,
        source_dir: str,
        directory: str,
        api_content_toc: dict
):
    for markdown_filepath in glob.glob(os.path.join(source_dir, directory, "*.md")):

        page_filename, page_ext = os.path.splitext(os.path.basename(markdown_filepath))
        realarb = os.path.relpath(os.path.dirname(markdown_filepath), source_dir)

        # already processed
        if page_filename == "index":
            continue

        url = "https://docs.microsoft.com/en-us/windows/win32/api/{0:s}/{1:s}".format(realarb, page_filename)
        filepath = os.path.join(
            download_dir,
            "docs.microsoft.com/en-us/windows/win32/api/{0:s}/{1:s}.html".format(realarb, page_filename)
        )
        logger.info("[+] download page %s  -> %s " % (url, filepath))
        success = download_textfile(url, filepath)

        if not success:
            logger.info("[X] could not download page %s  -> %s " % (url, filepath))
            continue

        url_relpath = "/windows/win32/api/{0:s}/{1:s}".format(realarb, page_filename)
        page_title = _findname(api_content_toc['toc'][directory]['items'][0], url_relpath)
        # logger.info("[+] %s => title '%s'" % (url_relpath, page_title))

        if page_filename.startswith("nc-"):
            category = "callbacks"
        elif page_filename.startswith("ne-"):
            category = "enums"
        elif page_filename.startswith("nf-"):
            category = "functions"
        elif page_filename.startswith("nn-"):
            category = "interfaces"
        elif page_filename.startswith("ns-"):
            category = "structures"
        elif page_filename.startswith("nl-"):
            category = "classes"
        else:
            category = "entries"

        api_content_toc[category].append(
            {
                'name': page_title,
                'path': "docs.microsoft.com/en-us{0:s}.html".format(url_relpath),
            }
        )

    return api_content_toc


def crawl_sdk_api_contents(configuration: Configuration, download_dir: str, source_dir: str):
    """ Download sdk-api entries based on TOC """

    api_content_toc = {
        'categories': [],
        'files': [],
        'callbacks': [],
        'functions': [],
        'enums': [],
        'interfaces': [],
        'structures': [],
        'classes': [],

        'entries': [],
        'toc': {}
    }

    content_dir = os.path.join(source_dir, "sdk-api-docs", "sdk-api-src", "content")

    for directory in os.listdir(content_dir):

        # download toc for directory
        toc_url = "https://docs.microsoft.com/en-us/windows/win32/api/{0:s}/toc.json".format(directory)
        logger.info("[+] download toc for directory %s" % (toc_url))
        toc_r = requests.get(toc_url)
        if toc_r.status_code == 200:
            api_content_toc['toc'][directory] = json.loads(requests.get(toc_url).text)
        else:
            logger.warning("[!] directory %s has no TOC !" % (toc_url))

        # only index folders with a toc
        if not api_content_toc['toc'].get(directory, None):
            continue

        # "meta" directory
        if directory.startswith("_"):

            url = "https://docs.microsoft.com/en-us/windows/win32/api/{0:s}".format(
                directory,
            )
            filepath = os.path.join(
                download_dir,
                "docs.microsoft.com/en-us/windows/win32/api/{0:s}".format(directory),
                "index.html"
            )
            logger.info("[+] download page %s  -> %s " % (url, filepath))
            download_textfile(url, filepath)

            category_title = api_content_toc['toc'][directory]['items'][0]['toc_title']
            api_content_toc['categories'].append(
                {
                    'name': category_title,
                    'path': os.path.join(
                        "docs.microsoft.com/en-us/windows/win32/api/{0:s}".format(directory),
                        "index.html"
                    ),
                }
            )

        # directory generated from a file
        else:

            url = "https://docs.microsoft.com/en-us/windows/win32/api/{0:s}".format(
                directory,
            )
            filepath = os.path.join(
                download_dir,
                "docs.microsoft.com/en-us/windows/win32/api/{0:s}".format(directory),
                "index.html"
            )
            logger.info("[+] download page %s  -> %s " % (url, filepath))
            download_textfile(url, filepath)

            category_title = directory
            if api_content_toc['toc'].get(directory, None):
                category_title = api_content_toc['toc'][directory]['items'][0]['toc_title']

            api_content_toc['files'].append(
                {
                    'name': category_title,
                    'path': os.path.join(
                        "docs.microsoft.com/en-us/windows/win32/api/{0:s}".format(directory),
                        "index.html"
                    ),
                }
            )

        api_content_toc = crawl_sdk_api_folder(configuration, download_dir, content_dir, directory, api_content_toc)

    return api_content_toc


def crawl_msdn_contents(configuration: Configuration, download_dir: str, source_dir: str):
    """ Download MSDN modules and content pages based on TOC """

    content_toc = {
        'attributes': [],
        'classes': [],
        'entries': [],
        'guides': [],
        'toc': {},
    }

    # counter = 0
    for r, d, f in os.walk(os.path.join(source_dir, "win32-docs", "desktop-src"), topdown=True):

        # if counter >=2000:
        #     break

        for image_file in filter(lambda s: os.path.splitext(s)[1] in [".png", ".jpg", ".jpeg"], f):
            realarb = os.path.relpath(r, os.path.join(source_dir, "win32-docs", "desktop-src"))
            image_dir = os.path.join(download_dir, "docs.microsoft.com/win32", realarb)
            filepath = os.path.join(image_dir, image_file)

            os.makedirs(image_dir, exist_ok=True)
            shutil.copyfile(os.path.join(r, image_file), filepath)

        for markdown_file in filter(lambda s: os.path.splitext(s)[1] == ".md", f):
            page_filename, page_ext = os.path.splitext(markdown_file)

            realarb = os.path.relpath(r, os.path.join(source_dir, "win32-docs", "desktop-src"))
            url = "https://docs.microsoft.com/en-us/windows/win32/{0:s}/{1:s}".format(
                realarb,
                page_filename
            )

            # retrieve html of page
            page_dir = os.path.join(download_dir, "docs.microsoft.com/win32", realarb)
            filepath = os.path.join(page_dir, "%s.html" % page_filename)
            logger.debug("[+] download page %s  -> %s " % (url, filepath))
            download_textfile(url, filepath)

            # don't care about top level pages
            if realarb == '.':
                continue

            # First time navigating in this directory
            if realarb not in content_toc['toc'].keys():

                # download toc for page
                toc_url = "https://docs.microsoft.com/en-us/windows/win32/{0:s}/toc.json".format(
                    realarb
                )
                logger.info("[+] download toc for page %s" % (toc_url))

                toc_r = requests.get(toc_url)
                if toc_r.status_code != 200:

                    # Could not find a toc for this folder
                    content_toc['toc'][realarb] = {
                        'toc': {'items': [{}]}
                    }

                    content_toc['guides'].append(
                        {
                            'name': page_filename,
                            'path': os.path.join(os.path.relpath(page_dir, download_dir), "%s.html" % page_filename),
                        }
                    )

                else:
                    component_toc = json.loads(requests.get(toc_url).text)
                    item = component_toc['items'][0]
                    if "href" in item:
                        component_title = item['toc_title']
                        component_href = item['href']

                        content_toc['toc'][realarb] = {
                            'toc': component_toc
                        }

                        content_toc['guides'].append(
                            {
                                'name': component_title,
                                'path': os.path.join(
                                    os.path.relpath(page_dir, download_dir),
                                    "%s.html" % component_href
                                ),
                            }
                        )

            # Adding current page to content toc

            # Class page
            if "ADSchema" in realarb and page_filename.startswith("c-"):
                logger.info("[+] new class page %s" % (page_filename))

                page_title = _findname(content_toc['toc'][realarb]['toc']['items'][0], page_filename)
                if not page_title:
                    page_title = page_filename

                content_toc['classes'].append(
                    {
                        'name': page_title,
                        'path': os.path.relpath(filepath, download_dir),
                    }
                )

            # Attribute page
            elif "ADSchema" in realarb and page_filename.startswith("a-"):
                logger.debug("[+] new attribute page %s" % (page_filename))

                page_title = _findname(content_toc['toc'][realarb]['toc']['items'][0], page_filename)
                if not page_title:
                    page_title = page_filename

                content_toc['attributes'].append(
                    {
                        'name': page_title,
                        'path': os.path.relpath(filepath, download_dir),
                    }
                )

            # Generic entry
            elif realarb in content_toc['toc']:
                try:
                    page_title = _findname(content_toc['toc'][realarb]['toc']['items'][0], page_filename)
                    if not page_title:
                        page_title = page_filename

                    content_toc['entries'].append(
                        {
                            'name': page_title,
                            'path': os.path.relpath(filepath, download_dir),
                        }
                    )
                except Exception as e:
                    logger.warning("[!] could not find a name for page %s" % page_filename)
                    logger.warning("[!] %s" % e)


            # counter+=1

            # if counter >=2000:
            #     break

    return content_toc


def rewrite_soup(configuration: Configuration, soup, html_path: str, documents_dir: str):
    """ rewrite html contents by fixing links and remove unnecessary cruft """

    # Fix navigations links
    links = soup.findAll("a", {"data-linktype": "relative-path"})  # for modules and cmdlet pages
    link_pattern = re.compile(r"([\w\.\/-]+)")

    for link in links:

        href = link['href']
        fixed_href = href

        # go back to module
        # if href == "./?view=powershell-%s" % configuration.powershell_version:
        #     fixed_href = "./%s.html" % link.text

        # go to a relative page
        targets = link_pattern.findall(href)
        if not len(targets):  # badly formated 'a' link
            continue

        page_target = targets[0]
        if page_target[-1] == '/':  # module index
            fixed_href = "%sindex.html" % page_target
        else:
            fixed_href = "%s.html" % page_target

        if fixed_href != href:
            logger.info("link rewrite : %s -> %s " % (href, fixed_href))
            link['href'] = fixed_href

    # remove link to external references if we can't support it
    for abs_href in soup.findAll("a", {"data-linktype": "absolute-path"}):

        # some externals hrefs are like this win32 -> api:
        #   <a href="/en-us/windows/win32/api/activation/nn-activation-iactivationfactory" data-linktype="absolute-path">IActivationFactory</a>
        if abs_href['href'].startswith("/en-us/windows/win32/api/"):

            # remove prefixing /
            prefix, *abs_suffix = abs_href['href'].split("/")

            # strip .html if it exists
            html_uri, ext = os.path.splitext(
                os.path.relpath(html_path, os.path.join(documents_dir, "docs.microsoft.com"))
            )
            uri_target, ext = os.path.splitext(os.path.join("docs.microsoft.com", *abs_suffix))

            rel_href = os.path.relpath(uri_target, html_uri)

            # rel_href = os.path.relpath(full_url_target, full_url_html_page)
            if rel_href[-1] == '/':  # module index
                rel_href = "%sindex.html" % rel_href
            else:
                rel_href = "%s.html" % rel_href

            logger.info("link rewrite : %s -> %s " % (abs_href['href'], rel_href))
            abs_href['href'] = rel_href
            abs_href['data-linktype'] = "relative-path"

        # some externals hrefs are like this win32 -> win32 :
        # <a href="/en-us/windows/desktop/api/FileAPI/nf-fileapi-definedosdevicew" data-linktype="absolute-path"><strong>DefineDosDevice</strong></a>
        elif abs_href['href'].startswith("/en-us/windows/desktop/api/"):

            # rewrite /en-us/windows/desktop/api to /en-us/windows/win32/api
            prefix, abs_suffix = abs_href['href'].split("/en-us/windows/desktop/api/")

            # strip .html if it exists
            html_uri, ext = os.path.splitext(
                os.path.relpath(html_path, os.path.join(documents_dir, "docs.microsoft.com"))
            )
            uri_target, ext = os.path.splitext(
                os.path.join("docs.microsoft.com", "en-us", "windows", "win32", "api", abs_suffix)
            )

            rel_href = os.path.relpath(uri_target, html_uri)

            # rel_href = os.path.relpath(full_url_target, full_url_html_page)
            if rel_href[-1] == '/':  # module index
                rel_href = "%sindex.html" % rel_href
            else:
                rel_href = "%s.html" % rel_href

            logger.info("link rewrite : %s -> %s " % (abs_href['href'], rel_href))
            abs_href['href'] = rel_href
            abs_href['data-linktype'] = "relative-path"


        # some externals hrefs are like this win32 -> win32 :
        #   <a href="/en-us/windows/desktop/winauto/inspect-objects" data-linktype="absolute-path">Inspect</a>
        elif abs_href['href'].startswith("/en-us/windows/desktop/"):

            # rewrite /en-us/windows/desktop to /win32/
            prefix, abs_suffix = abs_href['href'].split("/en-us/windows/desktop/")

            # strip .html if it exists
            html_uri, ext = os.path.splitext(
                os.path.relpath(html_path, os.path.join(documents_dir, "docs.microsoft.com"))
            )
            uri_target, ext = os.path.splitext(os.path.join("docs.microsoft.com", "win32", abs_suffix))

            rel_href = os.path.relpath(uri_target, html_uri)

            # rel_href = os.path.relpath(full_url_target, full_url_html_page)
            if rel_href[-1] == '/':  # module index
                rel_href = "%sindex.html" % rel_href
            else:
                rel_href = "%s.html" % rel_href

            logger.info("link rewrite : %s -> %s " % (abs_href['href'], rel_href))
            abs_href['href'] = rel_href
            abs_href['data-linktype'] = "relative-path"

        # some externals hrefs are like this :
        #   <a href="/en-us/uwp/api/windows.ui.viewmanagement.uisettings.textscalefactorchanged" data-linktype="absolute-path">UISettings.TextScaleFactorChanged Event</a>
        elif abs_href['href'].startswith("/en-us/"):
            full_url_target = "https://docs.microsoft.com" + abs_href['href']
            abs_href['href'] = full_url_target

        # Remove every other linktype absolute since we don't know how to handle it
        else:
            # TODO : currently we don't replace it in order to show the broken urls
            # abs_href.replace_with(abs_href.text)
            pass

    # remove unsupported nav elements
    nav_elements = [
        ["nav", {"class": "doc-outline", "role": "navigation"}],
        ["ul", {"class": "breadcrumbs", "role": "navigation"}],
        ["div", {"class": "sidebar", "role": "navigation"}],
        ["div", {"class": "dropdown dropdown-full mobilenavi"}],
        ["p", {"class": "api-browser-description"}],
        ["div", {"class": "api-browser-search-field-container"}],
        ["div", {"class": "pageActions"}],
        ["div", {"class": "container footerContainer"}],
        ["div", {"class": "dropdown-container"}],
        ["div", {"class": "binary-rating-buttons"}],
        ["ul", {"class": "metadata page-metadata"}],
        ["div", {"data-bi-name": "pageactions"}],
        ["div", {"class": "page-action-holder"}],
        ["div", {"class": "header-holder"}],
        ["footer", {"data-bi-name": "footer", "id": "footer"}],
        ["div", {"class": "binary-rating-holder"}],
        ["div", {"id": "left-container"}],
    ]

    for nav in nav_elements:
        nav_class, nav_attr = nav

        for nav_tag in soup.findAll(nav_class, nav_attr):
            _ = nav_tag.extract()

    # remove script elems
    for head_script in soup.head.findAll("script"):
        _ = head_script.extract()

    # Extract and rewrite additionnal stylesheets to download
    ThemeResourceRecord = collections.namedtuple('ThemeResourceRecord', 'url, path')

    theme_output_dir = os.path.join(documents_dir, Configuration.domain)
    theme_resources = []

    for link in soup.head.findAll("link", {"rel": "stylesheet"}):
        uri_path = link['href'].strip()

        if not uri_path.lstrip('/').startswith(Configuration.default_theme_uri):
            continue

        # Construct (url, path) tuple
        css_url = "https://%s/%s" % (Configuration.domain, uri_path)
        css_filepath = os.path.join(theme_output_dir, uri_path.lstrip('/'))

        # Converting href to a relative link
        path = os.path.relpath(css_filepath, os.path.dirname(html_path))
        rel_uri = '/'.join(path.split(os.sep))
        link['href'] = rel_uri

        theme_resources.append(
            ThemeResourceRecord(
                url=css_url,
                path=os.path.relpath(css_filepath, documents_dir),  # stored as relative path
            )
        )

    return soup, set(theme_resources)


def rewrite_html_contents(configuration: Configuration, html_root_dir: str):
    """ rewrite every html file downloaded """

    additional_resources = set()

    for html_file in glob.glob("%s/**/*.html" % html_root_dir, recursive=True):
        logger.info("rewrite  html_file : %s" % (html_file))

        # Read content and parse html
        with open(html_file, 'r', encoding='utf8') as i_fd:
            html_content = i_fd.read()

        soup = bs(html_content, 'html.parser')

        # rewrite html
        soup, resources = rewrite_soup(configuration, soup, html_file, html_root_dir)
        additional_resources = additional_resources.union(resources)

        # Export fixed html
        fixed_html = soup.prettify("utf-8")
        with open(html_file, 'wb') as o_fd:
            o_fd.write(fixed_html)

    return additional_resources


def download_additional_resources(configuration: Configuration, documents_dir: str, resources_to_dl: set = set()):
    """ Download optional resources for "beautification """

    for resource in resources_to_dl:
        download_textfile(
            resource.url,
            os.path.join(documents_dir, resource.path)
        )

    # Download index start page
    src_index_filepath = os.path.join(documents_dir, Configuration.domain, "win32", "desktop-app-technologies.html")
    index_filepath = os.path.join(documents_dir, Configuration.domain, "win32", "index.html")
    shutil.copy(src_index_filepath, index_filepath)

    # soup = bs( configuration.webdriver.get_url_page(index_url), 'html.parser')
    # soup = rewrite_index_soup(configuration, soup, index_filepath, documents_dir)
    # fixed_html = soup.prettify("utf-8")
    # with open(index_filepath, 'wb') as o_fd:
    #     o_fd.write(fixed_html)

    # # Download module.svg icon for start page
    # icon_module_url  =     '/'.join(["https:/"   , Configuration.domain, "en-us", "media", "toolbars", "module.svg"])
    # icon_module_path = os.path.join(documents_dir, Configuration.domain, "en-us", "media", "toolbars", "module.svg")
    # download_binary(icon_module_url, icon_module_path)


def create_sqlite_database(configuration, content_toc, resources_dir, documents_dir):
    """ Indexing the html document in a format Dash can understand """

    def insert_into_sqlite_db(cursor, name, record_type, path):
        """ Insert a new unique record in the sqlite database. """
        try:
            cursor.execute('SELECT rowid FROM searchIndex WHERE path = ?', (path,))
            dbpath = cursor.fetchone()
            cursor.execute('SELECT rowid FROM searchIndex WHERE name = ?', (name,))
            dbname = cursor.fetchone()

            if dbpath is None and dbname is None:
                cursor.execute(
                    'INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?,?,?)',
                    (name, record_type, path)
                )
                logger.debug('DB add [%s] >> name: %s, path: %s' % (record_type, name, path))
            else:
                logger.debug('record exists')

        except:
            pass

    sqlite_filepath = os.path.join(resources_dir, "docSet.dsidx")
    if os.path.exists(sqlite_filepath):
        os.remove(sqlite_filepath)

    db = sqlite3.connect(sqlite_filepath)
    cur = db.cursor()
    cur.execute('CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);')
    cur.execute('CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);')

    mapping = {
        # win32 content
        "guides": "Guide",
        "attributes": "Attribute",
        "classes": "Class",
        "entries": "Entry",

        # api-sdk content
        "categories": "Category",
        "files": "File",

        'callbacks': "Callback",
        'functions': "Function",
        'enums': "Enum",
        'interfaces': "Interface",
        'structures': "Structure",

    }

    # import pdb;pdb.set_trace()
    for key in mapping.keys():

        for _value in content_toc.get(key, []):
            # path should be unix compliant
            value_path = _value['path'].replace(os.sep, '/')
            insert_into_sqlite_db(cur, _value['name'], mapping[key], value_path)

            # commit and close db
    db.commit()
    db.close()


def copy_folder(src_folder: str, dst_folder: str):
    """ Copy a full folder tree anew every time """

    def onerror(func, path, exc_info):
        """
        Error handler for ``shutil.rmtree``.

        If the error is due to an access error (read only file)
        it attempts to add write permission and then retries.

        If the error is for another reason it re-raises the error.

        Usage : ``shutil.rmtree(path, onerror=onerror)``
        """
        import stat

        if not os.path.exists(path):
            return

        if not os.access(path, os.W_OK):
            # Is the error an access error ?
            os.chmod(path, stat.S_IWUSR)
            func(path)
        else:
            raise

    # print(dst_folder)
    shutil.rmtree(dst_folder, ignore_errors=False, onerror=onerror)
    shutil.copytree(src_folder, dst_folder)


def merge_folders(src, dst):
    if os.path.isdir(src):

        if not os.path.exists(dst):
            os.makedirs(dst)

        for name in os.listdir(src):
            merge_folders(
                os.path.join(src, name),
                os.path.join(dst, name)
            )
    else:
        shutil.copyfile(src, dst)


def main(configuration: Configuration):
    # """ Scheme for content toc :
    # {
    #     module_name : {
    #         'name' : str,
    #         'index' : relative path,
    #         'entries' : [
    #             {
    #                 'name' : str,
    #                 'path' : relative path, 
    #             },
    #             ...
    #         ]
    #     },
    #     ...
    # }
    # """
    content_toc = {}
    resources_to_dl = set()

    """ 0. Prepare folders """
    source_dir = os.path.join(configuration.build_folder, "_0_win32_source")
    api_source_dir = os.path.join(configuration.build_folder, "_0_api_sdk_source")

    download_dir = os.path.join(configuration.build_folder, "_1_downloaded_contents")
    html_rewrite_dir = os.path.join(configuration.build_folder, "_2_html_rewrite")
    additional_resources_dir = os.path.join(configuration.build_folder, "_3_additional_resources")
    package_dir = os.path.join(configuration.build_folder, "_4_ready_to_be_packaged")

    for folder in [source_dir, api_source_dir, download_dir, html_rewrite_dir, additional_resources_dir, package_dir]:
        os.makedirs(folder, exist_ok=True)

    # _4_ready_to_be_packaged is the final build dir
    docset_dir = os.path.join(package_dir, "%s.docset" % Configuration.docset_name)
    content_dir = os.path.join(docset_dir, "Contents")
    resources_dir = os.path.join(content_dir, "Resources")
    document_dir = os.path.join(resources_dir, "Documents")

    if conf.crawl_contents:
        # cloning source directories for scraping contents, extremely long operation
        logger.info(
            "Downloading win32 markdown zipped sources : %s -> %s" % (
            "https://github.com/MicrosoftDocs/win32/archive/refs/heads/docs.zip", os.path.join(source_dir, "docs.zip"))
        )
        download_binary(
            "https://github.com/MicrosoftDocs/win32/archive/refs/heads/docs.zip",
            os.path.join(source_dir, "docs.zip")
        )

        logger.info("Extracting win32 markdown zipped sources : ")
        with zipfile.ZipFile(os.path.join(source_dir, "docs.zip"), 'r') as zip_ref:
            zip_ref.extractall(source_dir)

        logger.info(
            "Downloading sdk-api markdown zipped sources : %s -> %s" % (
            "https://github.com/MicrosoftDocs/win32/archive/refs/heads/docs.zip", os.path.join(source_dir, "docs.zip"))
        )
        download_binary(
            "https://github.com/MicrosoftDocs/sdk-api/archive/refs/heads/docs.zip",
            os.path.join(api_source_dir, "docs.zip")
        )

        logger.info("Extracting api-sdk markdown zipped sources : ")
        with zipfile.ZipFile(os.path.join(api_source_dir, "docs.zip"), 'r') as zip_ref:
            zip_ref.extractall(api_source_dir)

        """ 1. Download html pages """
        logger.info("[1] scraping win32 web contents")
        content_toc = {}
        content_toc = crawl_msdn_contents(configuration, download_dir, source_dir)

        logger.info("[1] scraping sdk-api web contents")
        api_content_toc = crawl_sdk_api_contents(configuration, download_dir, api_source_dir)

        # Merge win32 api content
        content_toc.update(api_content_toc)
        with open(os.path.join(download_dir, "toc.json"), "w") as content:
            json.dump(content_toc, content)
    else:
        # print(os.path.join(download_dir, "toc.json"))
        with open(os.path.join(download_dir, "toc.json"), "r") as content:
            content_toc = json.load(content)

    """ 2.  Parse and rewrite html contents """
    logger.info("[2] rewriting urls and hrefs")
    copy_folder(download_dir, html_rewrite_dir)
    resources_to_dl = rewrite_html_contents(configuration, html_rewrite_dir)

    """ 3.  Download additionnal resources """
    logger.info("[3] download style contents")
    copy_folder(html_rewrite_dir, additional_resources_dir)
    download_additional_resources(configuration, additional_resources_dir, resources_to_dl)

    """ 4.  Database indexing """
    logger.info("[4] indexing to database")
    copy_folder(additional_resources_dir, document_dir)
    create_sqlite_database(configuration, content_toc, resources_dir, document_dir)

    """ 5.  Archive packaging """
    src_dir = os.path.dirname(__file__)
    shutil.copy(os.path.join(src_dir, "static/Info.plist"), content_dir)
    shutil.copy(os.path.join(src_dir, "static/DASH_LICENSE"), os.path.join(resources_dir, "LICENSE"))
    shutil.copy(os.path.join(src_dir, "static/icon.png"), docset_dir)
    shutil.copy(os.path.join(src_dir, "static/icon@2x.png"), docset_dir)

    output_dir = os.path.dirname(configuration.output_filepath)
    os.makedirs(output_dir, exist_ok=True)

    logger.info("[5] packaging as a dash docset")
    make_docset(
        docset_dir,
        configuration.output_filepath,
        Configuration.docset_name
    )


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Dash docset creation script for MSDN's Win32 API"
    )

    parser.add_argument(
        "-vv", "--verbose",
        help="increase output verbosity",
        action="store_true"
    )

    subparsers = parser.add_subparsers(help='sub-command help', dest='command')

    parser_create = subparsers.add_parser('create_docset', help='scrap the internet in order to create a docset')
    parser_create.add_argument(
        "-t", "--temporary",
        help="Use a temporary directory for creating docset, otherwise use current dir.",
        default=False,
        action="store_true"
    )

    parser_create.add_argument(
        "-o", "--output",
        help="set output filepath",
        default=os.path.join(os.getcwd(), "MSDN.tgz"),
    )

    parser_create.add_argument(
        "-s", "--sampling",
        help="generate only a 'sample' docset, in order to test if the rewriting rules are corrects",
        default=False,
        action="store_true"
    )

    parser_rewrite = subparsers.add_parser('rewrite_html', help='rewrite html file in order to test rules')

    parser_rewrite.add_argument(
        "input",
        help="set input filepath"
    )

    parser_rewrite.add_argument(
        "output",
        help="set output filepath"
    )

    parser_rewrite.add_argument(
        "html_root_dir",
        help="set html_root_dir filepath"
    )

    args = parser.parse_args()
    #if args.verbose:
        # logger.basicConfig(level=logger.DEBUG)
    logging.getLogger("requests").setLevel(logger.WARNING)
    logging.getLogger("urllib3").setLevel(logger.WARNING)
    #else:
    #    logger.basicConfig(level=logger.INFO)

    if args.command == "rewrite_html":

        conf = Configuration(args)

        # Read content and parse html
        with open(args.input, 'r', encoding='utf8') as i_fd:
            html_content = i_fd.read()

        soup = bs(html_content, 'html.parser')

        # rewrite html
        soup, resources = rewrite_soup(conf, soup, args.input, args.html_root_dir)

        # Export fixed html
        fixed_html = soup.prettify("utf-8")
        with open(args.output, 'wb') as o_fd:
            o_fd.write(fixed_html)

    elif args.command == "create_docset":
        conf = Configuration(args)

        if args.temporary:

            with tempfile.TemporaryDirectory() as tmp_builddir:
                conf.build_folder = tmp_builddir
                main(conf)
        else:
            main(conf)

    else:
        raise NotImplementedError("command not implemented %s" % args.command)
