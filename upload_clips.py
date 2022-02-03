#!/usr/bin/env python
# coding: utf-8

import json
import os
import random
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pickle
import requests
from dotenv import load_dotenv
from loguru import logger
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc


class LimitReached(Exception):
    pass


def get_posts():
    subreddit = os.environ['SUBREDDIT']
    proxies_file = os.environ['PROXIES_FILE']

    with open(proxies_file) as j:
        proxies = json.load(j)

    start = time.time()
    while True:
        if time.time() - start > 60:
            logger.error('Timed out...')
            break
        proxy = random.choice(proxies)
        proxy = f'http://{proxy["ip"]}:{proxy["port"]}'
        api_url = f'https://old.reddit.com/r/{subreddit}/top/.json'
        resp = requests.get(api_url, proxies={'http': proxy})

        if resp.status_code == 200:
            logger.info(f'Using proxy: {proxy}')
            return resp.json()
        else:
            logger.debug(f'Trying the next proxy: {proxy}')
            time.sleep(1)


def handle_post(child, data_file):
    title = child['data']['title']
    url = child['data']['url']
    if 'clips.twitch.tv' not in url:
        return
    data_file.seek(0)
    if url not in [x.rstrip() for x in data_file.readlines()]:
        pass
    else:
        return

    info_cmd = shlex.split(f'twitch-dl info "{url}" --json')
    info_p = subprocess.run(info_cmd,
                            shell=False,
                            check=False,
                            capture_output=True,
                            text=True)
    if info_p.returncode != 0:
        return

    info_resp = json.loads(info_p.stdout)
    suffix = Path(info_resp['videoQualities'][1]['sourceURL']).suffix
    channel = info_resp['broadcaster']['displayName']
    game = info_resp['game']['name']

    file_name = f'(Clip) {channel} - {title}{suffix}'

    ps = subprocess.Popen(['echo', 'n'], stdout=subprocess.PIPE)
    cmd = shlex.split(
        f'twitch-dl download "{url}" -q "720p" --output "{file_name}"')
    logger.info(f'Downloading: {url}')
    p = subprocess.run(cmd,
                       shell=False,
                       check=False,
                       capture_output=True,
                       text=True,
                       stdin=ps.stdout)
    if p.returncode != 0:
        return
    logger.info(f'Downloaded: {file_name}')

    tags = ['lsf', 'livestreamfails', 'twitch', channel, game]
    return file_name, tags, url


def save_cookies(driver, path):
    with open(path, 'wb') as filehandler:
        pickle.dump(driver.get_cookies(), filehandler)


def load_cookies(driver, path):
    with open(path, 'rb') as cookiesfile:
        cookies = pickle.load(cookiesfile)
        for cookie in cookies:
            driver.add_cookie(cookie)
    return driver


def upload(file_name, tags):
    channel_id = os.environ['CHANNEL_ID']
    driver.get(
        f'https://studio.youtube.com/channel/{channel_id}/videos?d=ud'
    )
    time.sleep(2)
    for e in driver.find_elements(By.TAG_NAME, 'input'):
        if e.get_attribute('type') == 'file':
            file = str(Path(file_name).absolute())
            e.send_keys(file)
            break
    try:
        title, description = WebDriverWait(driver, 20).until(
            EC.visibility_of_all_elements_located((By.ID, 'textbox')))
        title.click()
        ActionChains(driver).key_down(Keys.COMMAND).send_keys('A').key_up(
            Keys.COMMAND).send_keys(Keys.BACKSPACE).send_keys(
                Path(file_name).stem).perform()

        description.click()
        description.send_keys(f'{tags[-2]}\nLive Stream Fails (LSF) clip')

        driver.find_element(By.ID, 'toggle-button').click()

        for e in WebDriverWait(driver, 20).until(
                EC.visibility_of_all_elements_located((By.ID, 'text-input'))):
            if e.get_attribute('aria-label') == 'Tags':
                e.send_keys(','.join(tags) + ',')

        driver.find_element(By.ID, 'step-badge-3').click()
        time.sleep(2)

        driver.find_element(By.ID, 'done-button').click()

        logger.info(f'Uploading: {file_name}')

        for _ in range(120):
            try:
                driver.find_elements(By.CLASS_NAME, 'progress-label')[1].text
                logger.info(f'Uploaded: {file_name}')
            except IndexError:
                time.sleep(1)

        return driver
    except ElementClickInterceptedException:
        if 'Daily upload limit reached' in driver.find_element(
                By.CLASS_NAME, 'error-area').text:
            driver.close()
            raise LimitReached('Daily limit reached! Terminating...')


def login():
    options = uc.ChromeOptions()
    options.add_argument('--no-first-run --no-service-autorun')
    driver = uc.Chrome(options=options)
    # options.add_argument('headless')
    driver.get('https://youtube.com')
    time.sleep(3)
    cookies_file = os.environ['YOUTUBE_COOKIES_FILE']
    if not cookies_file:
        logger.warning('You don\'t have a cookies file in the `.env` file to login to YouTube!  Will attempt to generate one...')
        input('Press ENTER if you have logged in to your YouTube account.')
        save_cookies(driver, 'youtube_cookies.pkl')
    load_cookies(driver, cookies_file)
    time.sleep(3)
    return driver


if __name__ == '__main__':
    logger.add('logs.log')
    load_dotenv()

    if not Path('data_file.txt').exists():
        Path('data_file.txt').touch()

    data_file = open('data.txt', 'a+')
    data = get_posts()

    driver = login()

    for child in data['data']['children']:
        try:
            file_name, tags, url = handle_post(child, data_file)
            try:
                driver = upload(file_name, tags)
            except LimitReached:
                os.remove(file_name)
                sys.exit(0)
            data_file.write(url + '\n')
            os.remove(file_name)
        except (ValueError, TypeError):
            continue

    data_file.close()
    driver.close()
