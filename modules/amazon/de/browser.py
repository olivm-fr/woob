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

from ..en.browser import AmazonEnBrowser


class AmazonDeBrowser(AmazonEnBrowser):
    BASEURL = 'https://www.amazon.de'
    CURRENCY = 'EUR'
    LANGUAGE = 'en-GB'

    # it's in english even in for this browser
    WRONGPASS_MESSAGES = [
        'Your password is incorrect',
        'We cannot find an account with that e-mail address',
        'We cannot find an account with that mobile number',
    ]
    WRONG_CAPTCHA_RESPONSE = "Enter the characters as they are shown in the image."
