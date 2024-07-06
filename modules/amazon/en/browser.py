# Copyright(C) 2017      Théo Dorée
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from ..browser import AmazonBrowser


class AmazonEnBrowser(AmazonBrowser):
    BASEURL = 'https://www.amazon.com'
    CURRENCY = '$'
    LANGUAGE = 'en-US'

    L_SIGNIN = 'Sign in'
    L_LOGIN = 'Login'
    L_SUBSCRIBER = 'Name: (.*) Edit E'

    UNSUPPORTED_TWOFA_MESSAGE = (
        "This strong authentication method is not supported. "
        + "Please disable the Two-Step Verification before retrying."
    )

    WRONGPASS_MESSAGES = [
        'Your password is incorrect',
        'We cannot find an account with that email address',
        'Enter a valid email or mobile number',
        'We cannot find an account with that mobile number',
    ]
    WRONG_CAPTCHA_RESPONSE = "Enter the characters as they are given in the challenge."
