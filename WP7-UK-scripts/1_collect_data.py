#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import time
import datetime

import requests
from bs4 import BeautifulSoup

from utils import Mongo

from urllib.parse import parse_qs, urlparse


FACEBOOK_GRAPH_URL = "https://graph.facebook.com/"
access_token = "xxxxx" # Get an access token from Facebook

class GraphAPI(object):
    def __init__(self, access_token=None, version=None):
        self.access_token = access_token
        self.session = requests.Session()
        if version:
            self.version = 'v' + version
        else:
            self.version = 'v2.8'
    def get_object(self, id, **kwargs):
        return self.request("{0}/{1}".format(self.version, id), kwargs)
    def get_connections(self, id, connection_name, **kwargs):
        return self.request("{0}/{1}/{2}".format(self.version, id, connection_name), kwargs)
    def get_all_connections(self, id, connection_name, **kwargs):
        """Get all pages from a get_connections call
        This will iterate over all pages returned by a get_connections call
        and yield the individual items.
        """
        while True:
            page = self.get_connections(id, connection_name, **kwargs)
            for item in page['data']:
                yield item
            next = page.get('paging', {}).get('next')
            if not next:
                return
            kwargs = parse_qs(urlparse(next).query)
            del kwargs['access_token']
    def request(self, path, args=None):
        if args is None:
            args = dict()
        if 'access_token' not in args:
            args['access_token'] = self.access_token
        try:
            response = self.session.request(
                'GET',
                FACEBOOK_GRAPH_URL + path,
                params=args)
            print response.status_code
            time.sleep(1)
        except requests.HTTPError as e:
            raise
        headers = response.headers
        if 'json' in headers['content-type']:
            result = response.json()
        return result

def process_comment(c):
    item = dict()
    item['comment_id'] = c['id']
    item['post_id'] = c['post_id']
    item['created_time'] = c['created_time']
    item['comment_count'] = c['comment_count']
    item['like_count'] = c['like_count']
    item['message'] = c['message']
    item['user'] = c['from']
    if c.get('parent', None):
        item['parent_id'] = c['parent']['id']
    return item

def process_post(p):
    item = dict()
    item['post_id'] = p['id']
    item['article_url'] = p.get('link', None)
    item['created_time'] = p['created_time']
    item['comment_count'] = p['comments']['summary']['total_count']
    if p.get('shares', None):
        item['share_count'] = p['shares']['count']
    item['message'] = p['message']
    item['reactions'] = { 'total_count': p['total']['summary']['total_count'],
                          'like': p['like']['summary']['total_count'],
                          'angry': p['angry']['summary']['total_count'],
                          'haha': p['haha']['summary']['total_count'],
                          'love': p['love']['summary']['total_count'],
                          'sad': p['sad']['summary']['total_count'],
                          'wow': p['wow']['summary']['total_count'],
                          'thankful': p['thankful']['summary']['total_count']
    }
    return item

# Get extra information from the Guardian website
def get_extra(url):
    if url is None:
        return None
    if not urlparse(url).netloc == 'www.theguardian.com':
        return None
    response = requests.get(url)
    time.sleep(2)
    print "Response received from %s" %url
    soup = BeautifulSoup(response.text, "lxml")
    tags = [tag.text.strip() for tag in soup.findAll('a', attrs={'class': 'submeta__link'})]
    article_title = soup.find(attrs={'itemprop': 'headline'})
    if article_title:
        article_title= article_title.text.strip()
    authors = [author.text.strip() for author in soup.findAll('span', attrs={'itemprop': 'author'})]
    categories = list({category.text.strip().lower() for category in soup.findAll('a', attrs={'class': 'signposting__action'})})
    main_category = re.search(re.compile(r'theguardian\.com\/([\w-]*)'), url).group(1)
    return {'tags':tags, 'article_title':article_title,
            'authors': authors, 'categories': categories,
            'main_category' : main_category}



if __name__ == "__main__":

    # Example
    ### Data collection
    DAYS_DIFF = 6

    # Calculate timestamps for start/end of collection (24 hour period)
    epoch = datetime.datetime.utcfromtimestamp(0)
    today = datetime.datetime.combine(datetime.date.today(),
                                      datetime.time(0,0))

    def get_epochs(dd):
        date = today - datetime.timedelta(dd)
        delta = date - epoch
        return "{:.0f}".format(delta.total_seconds())


    day_start, day_end = [get_epochs(x) for x in (DAYS_DIFF, DAYS_DIFF-1)]



    # Collect
    guardian_id = '10513336322'

    graph = GraphAPI(access_token)
    guardian_posts = graph.get_all_connections(guardian_id, 'posts',
                                               since=day_start, until=day_end,
                                               limit=100,
                                               fields='message,created_time,id,link,shares,comments.limit(0).summary(total_count)')
    comments_list = []
    posts_list = []

    # Collect Post data
    for post in guardian_posts:
        post_id = post['id']
        reactions = graph.get_object(post_id, fields='reactions.type(LIKE).limit(0).summary(total_count).as(like),reactions.type(LOVE).limit(0).summary(total_count).as(love),reactions.type(WOW).limit(0).summary(total_count).as(wow),reactions.type(HAHA).limit(0).summary(total_count).as(haha),reactions.type(SAD).limit(0).summary(total_count).as(sad),reactions.type(ANGRY).limit(0).summary(total_count).as(angry),reactions.type(THANKFUL).limit(0).summary(total_count).as(thankful),reactions.type(NONE).limit(0).summary(total_count).as(total)')
        post.update(reactions)
        post = process_post(post)
        posts_list.append(post)

    # Adding extra bits from the guardian
    for post in posts_list:
        extra = get_extra(post['article_url'])
        if extra:
            post.update(extra)

    # Inserting posts collected in Mongo
    mongo = Mongo('facebook', 'posts')
    for post in posts_list:
        mongo.process_item(post)
    mongo.close()
    del mongo

    # Collect Comments data
    for idx, post in enumerate(posts_list):
        post_id = post['post_id']
        print "Extracting %d comments for post %d ..." %(post['comment_count'], idx)
        comments = graph.get_all_connections(post_id, 'comments',
                                             limit=100,
                                             fields='created_time,from,like_count,message,id,comment_count')
        for comment in comments:
            comment.update({'post_id':post_id})
            comment = process_comment(comment)
            comments_list.append(comment)
            if comment.get('comment_count',0) > 0:
                second_level_comments = graph.get_all_connections(comment['comment_id'], 'comments',
                                             limit=100,
                                             fields='created_time,from,like_count,message,id,comment_count,parent')
                for second_level_comment in second_level_comments:
                    second_level_comment.update({'post_id':post_id})
                    second_level_comment = process_comment(second_level_comment)
                    comments_list.append(second_level_comment)

    # Inserting comments collected in Mongo
    mongo = Mongo('facebook', 'comments')
    for comment in comments_list:
        mongo.process_item(comment)
    mongo.close()
    del mongo
