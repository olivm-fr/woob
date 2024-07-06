# -*- coding: utf-8 -*-

# Copyright(C) 2018      Vincent A
# Copyright(C) 2018-2023 Budget Insight
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from base64 import b64encode

from urllib3.exceptions import ReadTimeoutError

from woob.browser.browsers import APIBrowser
from woob.exceptions import BrowserIncorrectPassword, ScrapingBlocked
from woob.capabilities.captcha import (
    ImageCaptchaJob, RecaptchaJob, RecaptchaV3Job, RecaptchaV2Job,
    FuncaptchaJob, HcaptchaJob, CaptchaError, InsufficientFunds,
    UnsolvableCaptcha, InvalidCaptcha, GeetestV4Job, TurnstileJob,
)
from woob.tools.json import json


class AnticaptchaBrowser(APIBrowser):
    BASEURL = 'https://api.anti-captcha.com/'

    def __init__(self, apikey, captcha_proxy, *args, **kwargs):
        super(AnticaptchaBrowser, self).__init__(*args, **kwargs)
        self.apikey = apikey
        self.captcha_proxy = captcha_proxy

    def post_image(self, data):
        data = {
            "clientKey": self.apikey,
            "task": {
                "type": "ImageToTextTask",
                "body": b64encode(data).decode('ascii'),
                "phrase": False,
                "case": False,
                "numeric": False,
                "math": 0,
                "minLength": 0,
                "maxLength": 0,
            },
        }
        r = self.request('/createTask', data=data)
        self.check_reply(r)
        return str(r['taskId'])

    def post_recaptcha(self, url, key):
        return self.post_gcaptcha(url, key, 'RecaptchaV1')

    def post_recaptchav2(self, url, key):
        return self.post_gcaptcha(url, key, 'RecaptchaV2')

    def post_hcaptcha(self, url, key):
        return self.post_gcaptcha(url, key, 'HCaptcha')

    def post_gcaptcha(self, url, key, prefix):
        data = {
            "clientKey": self.apikey,
            "task": {
                "type": "%sTaskProxyless" % prefix,
                "websiteURL": url,
                "websiteKey": key,
            },
            "softId": 0,
            "languagePool": "en",
        }
        r = self.request('/createTask', data=data)
        self.check_reply(r)
        return str(r['taskId'])

    def post_gcaptchav3(self, url, key, action, min_score, is_enterprise):
        # Regarding the min_score, the acceptable values can be found in:
        # https://anti-captcha.com/fr/apidoc/task-types/RecaptchaV3TaskProxyless
        if min_score is None:
            min_score = 0.3
        if min_score not in [0.3, 0.7, 0.9]:
            raise ValueError('The reCaptcha minimum score must be 0.3, 0.7 or 0.9')
        data = {
            "clientKey": self.apikey,
            "task": {
                "type": "RecaptchaV3TaskProxyless",
                "websiteURL": url,
                "websiteKey": key,
                "minScore": min_score,
                "pageAction": action,
                "isEnterprise": is_enterprise,
            },
        }
        r = self.request('/createTask', data=data)
        self.check_reply(r)
        return str(r['taskId'])

    def post_funcaptcha(self, url, key, sub_domain, additional_data=None):
        data = {
            "clientKey": self.apikey,
            "task": {
                "type": "FunCaptchaTaskProxyless",
                "websiteURL": url,
                "funcaptchaApiJSSubdomain": sub_domain,
                "websitePublicKey": key,
            },
            "softId": 0,
            "languagePool": "en",
        }
        if additional_data:
            data['task']['data'] = json.dumps(
                additional_data,
                separators=(',', ':'),
            )

        r = self.request('/createTask', data=data)
        self.check_reply(r)
        return str(r['taskId'])

    def post_geetestv4(self, url, gt):
        data = {
            "clientKey": self.apikey,
            "task": {
                "type": "GeeTestTaskProxyless",
                "websiteURL": url,
                "gt": gt,
                "version": 4,
            },
            "softId": 0,
            "languagePool": "en",
        }
        r = self.request('/createTask', data=data)
        self.check_reply(r)
        return str(r['taskId'])

    def post_turnstile(self, url, key):
        data = {
            "clientKey": self.apikey,
            "task": {
                "type": "TurnstileTaskProxyless",
                "websiteURL": url,
                "websiteKey": key,
            },
        }
        r = self.request('/createTask', data=data)
        self.check_reply(r)
        return str(r['taskId'])

    def check_reply(self, r):
        excs = {
            'ERROR_KEY_DOES_NOT_EXIST': BrowserIncorrectPassword,
            'ERROR_PROXY_NOT_AUTHORISED': BrowserIncorrectPassword,
            'ERROR_ZERO_CAPTCHA_FILESIZE': InvalidCaptcha,
            'ERROR_TOO_BIG_CAPTCHA_FILESIZE': InvalidCaptcha,
            'ERROR_IMAGE_TYPE_NOT_SUPPORTED': InvalidCaptcha,
            'ERROR_RECAPTCHA_INVALID_SITEKEY': InvalidCaptcha,
            'ERROR_RECAPTCHA_INVALID_DOMAIN': InvalidCaptcha,
            'ERROR_ZERO_BALANCE': InsufficientFunds,
            'ERROR_CAPTCHA_UNSOLVABLE': UnsolvableCaptcha,
            'ERROR_IP_BLOCKED': ScrapingBlocked,
            'ERROR_PROXY_BANNED': ScrapingBlocked,
        }

        if not r['errorId']:
            return

        code = r.get('errorCode')
        description = r.get('errorDescription')
        self.logger.debug('Captcha Error: %s, %s', code, description)
        exc_type = excs.get(code, CaptchaError)
        raise exc_type(f"{code}: {description}")

    def poll(self, job):
        data = {
            "clientKey": self.apikey,
            "taskId": int(job.id),
        }

        try:
            r = self.request('/getTaskResult', data=data)
        except ReadTimeoutError:
            # Just skip the current poll.
            return False

        self.check_reply(r)

        if r['status'] != 'ready':
            return False

        sol = r['solution']
        if isinstance(job, ImageCaptchaJob):
            job.solution = sol['text']
        elif isinstance(job, RecaptchaJob):
            job.solution = sol['recaptchaResponse']
            job.solution_challenge = sol['recaptchaChallenge']
        elif isinstance(job, (RecaptchaV2Job, RecaptchaV3Job, HcaptchaJob)):
            job.solution = sol['gRecaptchaResponse']
        elif isinstance(job, FuncaptchaJob):
            job.solution = sol['token']
        elif isinstance(job, GeetestV4Job):
            job.solution = sol
        elif isinstance(job, TurnstileJob):
            job.solution = sol['token']
        else:
            raise NotImplementedError()

        return True

    def get_balance(self):
        data = {
            "clientKey": self.apikey,
        }
        r = self.request('/getBalance', data=data)
        self.check_reply(r)
        return r['balance']

    def report_wrong_image(self, job):
        data = {
            "clientKey": self.apikey,
            "taskId": int(job.id),
        }
        r = self.request('/reportIncorrectImageCaptcha', data=data)
        self.logger.debug('complaint accepted? %s', r['errorId'] == 0)

    def report_wrong_recaptcha(self, job):
        data = {
            "clientKey": self.apikey,
            "taskId": int(job.id),
        }
        r = self.request('/reportIncorrectRecaptcha ', data=data)
        self.logger.debug('complaint accepted? %s', r['errorId'] == 0)
