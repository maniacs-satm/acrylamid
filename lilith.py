#!/usr/bin/env python
# -*- encoding: utf-8 -*-
#
# Copyright 2011 posativ <info@posativ.org>. All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
#    1. Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
# 
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
# 
# The views and conclusions contained in the software and documentation are
# those of the authors and should not be interpreted as representing official
# policies, either expressed or implied, of posativ <info@posativ.org>.

VERSION = "0.1-stable"
VERSION_SPLIT = tuple(VERSION.split('-')[0].split('.'))

import sys
reload(sys); sys.setdefaultencoding('utf-8')

import os
import re
import locale
import time
from datetime import datetime
import logging
import fnmatch

import yaml
import extensions
import tools
from tools import FileEntry, ColorFormatter

from shpaml import convert_text
from jinja2 import Template

class Lilith:
    """Main class for Lilith functionality.  It handles initialization,
    defines default behavior, and also pushes the request through all
    steps until the output is rendered and we're complete."""
    
    def __init__(self, conf, env=None, data=None):
        """Sets configuration and environment and creates the Request
        object
        
        config -- dict containing the configuration variables
        environ -- dict containing the environment variables
        """
        
        conf['lilith_name'] = "lilith"
        conf['lilith_version'] = VERSION
        
        conf['output_dir'] = conf.get('output_dir', 'output/')
        conf['entries_dir'] = conf.get('entries_dir', 'content/')
        conf['layout_dir'] = conf.get('layout_dir', 'layouts/')
        
        self._config = conf
        self._data = data
        self._env = env
        self.request = Request(conf, env, data)
        
    def initialize(self):
        """The initialize step further initializes the Request by
        setting additional information in the ``data`` dict,
        registering plugins, and entryparsers.
        """
        data = self._data
        conf = self._config

        # initialize the locale, will silently fail if locale is not
        # available and uses system's locale
        try:
            locale.setlocale(locale.LC_ALL, conf.get('lang', False))
        except (locale.Error, TypeError):
            # invalid locale
            log.warn('unsupported locale `%s`' % conf['lang'])
            locale.setlocale(locale.LC_ALL, '')
            conf['lang'] = locale.getlocale()

        if conf.get('www_root', None):
            conf['www_root'] = conf.get('www_root', '')
            conf['protocol'] = 'https' if conf['www_root'].find('https') == 0 \
                                      else 'http'
        else:
            log.warn('no `www_root` specified, using localhost:8000')
            conf['www_root'] = 'http://localhost:8000/'
            conf['protocol'] = 'http'

        # take off the trailing slash for base_url
        if conf['www_root'].endswith("/"):
            conf['www_root'] = conf['www_root'][:-1]

        # import and initialize plugins
        extensions.initialize(conf.get("ext_dir", ['ext', ]),
                              exclude=conf.get("ext_ignore", []),
                              include=conf.get("ext_include", []))
        
        conf['extensions'] = [ex.__name__ for ex in extensions.plugins]
    
    def run(self):
        """This is the main loop for lilith.  This method will run
        the handle callback to allow registered handlers to handle
        the request. If nothing handles the request, then we use the
        ``_lilith_handler``.
        """
        
        self.initialize()
        
        # run the start callback, initialize jinja2 template
        log.debug('cb_start')
        request = tools.run_callback(
                        'start',
                        self.request,
                        defaultfunc=_start)
        
        # run the default handler
        log.debug('cb_handle')
        request = tools.run_callback(
                        "handle",
                        request,
                        defaultfunc=_lilith_handler)
                
        # do end callback
        tools.run_callback('end', request)

            
class Request(object):
    """This class holds the lilith request.  It holds configuration
    information, OS environment, and data that we calculate and
    transform over the course of execution."""
    
    def __init__(self, conf, env, data):
        """Sets configuration and data.
        
        Arguments:
        config: dict containing configuration variables
        data: dict containing data variables"""
        
        self._data = data
        self._config = conf
        self._env = env

def _start(request):
    """this loads entry.html and main.html into data and does a little
    preprocessing: {{ include: identifier }} will be replaced by the
    corresponding identifier in request._env if exist else empty string."""
    
    from collections import defaultdict
    
    conf = request._config
    env = request._env
    data = request._data
    regex = re.compile('{{\s*include:\s+(\w+)\s*}}')
    entry = open(os.path.join(conf['layout_dir'], 'entry.html')).read()
    main = open(os.path.join(conf['layout_dir'], 'main.html')).read()
    
    d = defaultdict(str)
    d.update(env)
    data['tt_entry'] = re.sub(regex, lambda m: d[m.group(1)], entry)
    data['tt_main'] = re.sub(regex, lambda m: d[m.group(1)], main)
    
    return request

def _lilith_handler(request):
    """This is the default lilith handler.
        - cb_filelist
        - cb_filestat
        - cb_sortlist
        - cb_entryparser
            - cb_preformat
            - cb_format
            - cb_postformat
        - cb_prepare
    """
    conf = request._config
    data = request._data
                       
    # call the filelist callback to generate a list of entries
    #log.debug('cb_filelist')
    request =  tools.run_callback(
            "filelist",
            request,
            defaultfunc=_filelist)
        
    # chance to modify specific meta data e.g. datetime
    request = tools.run_callback(
            'filestat', 
            request,
            defaultfunc=_filestat)
    
    # use datetime to sort chronological
    request = tools.run_callback(
            'sortlist', 
            request,
            defaultfunc=_sortlist)
            
    # entry specific callbacks
    for i,entry in enumerate(request._data['entry_list']):
        request._data['entry_list'][i] = tools.run_callback(
                'entryparser',
                {'entry': entry, 'config': request._config},
                defaultfunc=_entryparser)
                
    request = tools.run_callback(
            'prepare',
            request,
            defaultfunc=_prepare)
    
    from copy import deepcopy # performance? :S
    
    tools.run_callback(
        'item',
        deepcopy(request),
        defaultfunc=_item)
    
    tools.run_callback(
        'page',
        deepcopy(request),
        defaultfunc=_page)
    
    return request

def _filelist(request):
    """This is the default handler for getting entries.  It takes the
    request object in and figures out which entries bases on the default
    behavior that we want to show and generates a list of EntryBase
    subclass objects which it returns.
    
    Arguments:
    args -- dict containing the incoming Request object
    
    Returns the content we want to render"""
    
    data = request._data
    conf = request._config
    
    filelist = []
    for root, dirs, files in os.walk(conf['entries_dir']):
        for file in files:
            path = os.path.join(root, file)
            fn = filter(lambda p: fnmatch.fnmatch(path, os.path.join(conf['entries_dir'], p)),
                        conf.get('entries_ignore', []))
            if not fn:
                filelist.append(path)
    
    entry_list = [FileEntry(request, e, conf['entries_dir']) for e in filelist]
    data['entry_list'] = entry_list
    
    return request
    
def _filestat(request):
    """Sets an alternative timestamp specified in yaml header."""
    
    conf = request._config
    entry_list = request._data['entry_list']
    
    for i in range(len(entry_list)):
        if entry_list[i].has_key('date'):
            timestamp = time.strptime(entry_list[i]['date'],
                                conf.get('strptime', '%d.%m.%Y, %H:%M'))
            timestamp = time.mktime(timestamp)
            entry_list[i].date = datetime.fromtimestamp(timestamp)
        else:
            entry_list[i]['date'] = entry_list[i]._date
            log.warn("using mtime from %s" % entry_list[i])
    return request
    
def _sortlist(request):
    """sort list by date"""

    entry_list = request._data['entry_list']
    entry_list.sort(key=lambda k: k.date, reverse=True)
    return request

def _entryparser(request):
    """Applies:
        - cb_preformat
        - cb_format
        - cb_postformat"""
    
    entry = request['entry']
    
    entry = tools.run_callback(
            'preformat',
            {'entry': entry, 'config': request['config']},
            defaultfunc=_preformat)['entry']

    entry = tools.run_callback(
            'format',
            {'entry': entry, 'config': request['config']},
            defaultfunc=_format)['entry']
        
    entry = tools.run_callback(
            'postformat',
            {'entry': entry, 'config': request['config']})['entry']
    
    return entry
    
def _preformat(request):
    """joins content from file (stored as list of strings)"""
    
    entry = request['entry']
    entry['body'] = ''.join(entry['story'])
    return request

def _format(request):
    """Apply markup using Post.parser."""
    
    entry = request['entry']
    conf = request['config']
    
    parser = entry.get('parser', conf.get('parser', 'plain'))
    
    if parser.lower() in ['markdown', 'mkdown', 'md']:
        from markdown import markdown
        entry['body'] = markdown(entry['body'],
                        extensions=['codehilite(css_class=highlight)'])
        return request
    
    elif parser.lower() in ['restructuredtest', 'rst', 'rest']:                
        from docutils import nodes
        from docutils.parsers.rst import directives, Directive
        from docutils.core import publish_parts
        
        # Set to True if you want inline CSS styles instead of classes
        INLINESTYLES = False
        
        from pygments.formatters import HtmlFormatter
        
        # The default formatter
        DEFAULT = HtmlFormatter(noclasses=INLINESTYLES)
        
        
        from docutils import nodes
        from docutils.parsers.rst import directives, Directive
        
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name, TextLexer
        
        class Pygments(Directive):
            """ Source code syntax hightlighting.
            """
            required_arguments = 1
            optional_arguments = 0
            final_argument_whitespace = True
            option_spec = dict([(key, directives.flag) for key in {}])
            has_content = True
    
            def run(self):
                self.assert_has_content()
                try:
                    lexer = get_lexer_by_name(self.arguments[0])
                except ValueError:
                    # no lexer found - use the text one instead of an exception
                    lexer = TextLexer()
                # take an arbitrary option if more than one is given
                formatter = self.options and VARIANTS[self.options.keys()[0]] or DEFAULT
                parsed = highlight(u'\n'.join(self.content), lexer, formatter)
                return [nodes.raw('', parsed, format='html')]
        
        directives.register_directive('sourcecode', Pygments)
        initial_header_level = 1
        transform_doctitle = 0
        settings = {
            'initial_header_level': initial_header_level,
            'doctitle_xform': transform_doctitle
            }
        parts = publish_parts(
            entry['body'], writer_name='html', settings_overrides=settings)
        entry['body'] = parts['body'].encode('utf-8')
        return request
    else:
        return request

def _prepare(request):
    """Sets a few required keys in FileEntry.
    
    required:
    safe_title -- used as directory name
    url -- permalink
    id -- atom conform id: http://diveintomark.org/archives/2004/05/28/howto-atom-id
    """
    conf = request._config
    data = request._data
    
    for i, entry in enumerate(data['entry_list']):
        safe_title = tools.safe_title(entry['title'])
        url = '/' + str(entry.date.year) + '/' + safe_title + '/'
        id = 'tag:' + re.sub('https?://', '', conf.get('www_root', '')).strip('/') \
             + ',' + entry.date.strftime('%Y-%m-%d') + ':' \
             + '/' + str(entry.date.year) +  '/' + safe_title
        item = data['entry_list'][i]
        if not 'safe_title' in item:
            item['safe_title'] = safe_title
        if not 'url' in item:
            item['url'] = url
        if not 'id' in item:
            item['id'] = id
    
    return request
    
def _item(request):
    """Creates single full-length entry.  Looks like
    http://yourblog.org/year/title/(index.html).
    
    required:
    entry.html -- layout of Post's entry
    main.html -- layout of the website
    """
    conf = request._config
    env = request._env
    data = request._data
    
    tt_entry = Template(data['tt_entry'])
    tt_main = Template(data['tt_main'])

    # last preparations
    request = tools.run_callback(
                'preitem',
                request)
    
    for entry in data['entry_list']:
        
        html = tools.render(tt_main, conf, env, type='item',
                            entry_list=tools.render(tt_entry, conf, env,
                                            entry, type='item') )
        
        directory = os.path.join(conf['output_dir'],
                         str(entry.date.year),
                         entry.safe_title)
        path = os.path.join(directory, 'index.html')
        tools.mk_file(html, entry, path)
        
    request = tools.run_callback(
                'postitem',
                request)
    
    return request

def _page(request):
    """Creates nicely paged listing of your posts.  First “Page” is the
    index.hml used to have this nice url: http://yourblog.com/ with a recent
    list of your (e.g. summarized) Posts. Other pages are enumerated to /page/n+1
    
    required:
    items_per_page -- posts displayed per page (defaults to 6)
    entry.html -- layout of Post's entry
    main.html -- layout of the website
    """
    conf = request._config
    env = request._env
    data = request._data
    ipp = conf.get('items_per_page', 6)
    
    # last preparations
    request = tools.run_callback(
                'prepage',
                request)
        
    tt_entry = Template(data['tt_entry'])
    tt_main = Template(data['tt_main'])
            
    entry_list = [tools.render(tt_entry, conf, env, entry, type="page")
                    for entry in data['entry_list']]
                
    for i, mem in enumerate([entry_list[x*ipp:(x+1)*ipp]
                                for x in range(len(entry_list)/ipp+1)] ):
                                    
        html = tools.render(tt_main, conf, env, type='page', page=i+1,
                    entry_list='\n'.join(mem), num_entries=len(entry_list))
        directory = os.path.join(conf['output_dir'],
                         '' if i == 0 else 'page/%s' % (i+1))
        path = os.path.join(directory, 'index.html')
        tools.mk_file(html, {'title': 'page/%s' % (i+1)}, path)
    
    request = tools.run_callback(
                'postpage',
                request)
    
    return request

if __name__ == '__main__':
    
    from optparse import OptionParser
    
    parser = OptionParser()
    parser.add_option("-c", "--config-file", dest="conf", metavar="FILE",
                      help="an alternative conf to use", default="lilith.conf")
    #parser.add_option("-o", "--overwrite", dest="force",
    #                  help="force overwrite", default=False)
    parser.add_option("-l", "--layout_dir", dest="layout", metavar="DIR",
                      help="force overwrite", default=False)
    parser.add_option("-q", "--quit", action="store_const", dest="verbose",
                      help="be silent (mostly)", const=logging.WARN,
                      default=logging.INFO)
#    parser.add_option("-v", "--verbose", action="store_const", dest="verbose",
#                      help="be verbose", const=logging.INFO)
    parser.add_option("--debug", action="store_const", dest="verbose",
                      help="debug information", const=logging.DEBUG)
    
    (options, args) = parser.parse_args()
    
    console = logging.StreamHandler()
    console.setFormatter(ColorFormatter('[%(levelname)s] %(name)s.py: %(message)s'))
    if options.verbose == logging.DEBUG:
        fmt = '%(msecs)d [%(levelname)s] %(name)s.py:%(lineno)s:%(funcName)s %(message)s'
        console.setFormatter(ColorFormatter(fmt))
    log = logging.getLogger('lilith')
    log.addHandler(console)
    log.setLevel(options.verbose)
        
    conf = yaml.load(open(options.conf).read())
    if options.layout:
        conf['layout_dir'] = options.layout
    assert tools.check_conf(conf)
    
    l = Lilith(conf=conf, env={}, data={})
    l.run()
