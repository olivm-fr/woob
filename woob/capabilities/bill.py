# Copyright(C) 2012 Romain Bignon, Florent Fourcot
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

import warnings

from woob.exceptions import BrowserIncorrectPassword

from .account import CapCredentialsCheck
from .base import (
    BaseObject,
    BoolField,
    Currency,
    DecimalField,
    DeprecatedFieldWarning,
    Enum,
    Field,
    StringField,
    UserError,
    empty,
    find_object,
)
from .collection import CapCollection
from .date import DateField


__all__ = [
    "SubscriptionNotFound",
    "DocumentNotFound",
    "DocumentTypes",
    "Detail",
    "Document",
    "Bill",
    "Subscription",
    "CapDocument",
]


class SubscriptionNotFound(UserError):
    """
    Raised when a subscription is not found.
    """

    def __init__(self, msg="Subscription not found"):
        super().__init__(msg)


class DocumentNotFound(UserError):
    """
    Raised when a document is not found.
    """

    def __init__(self, msg="Document not found"):
        super().__init__(msg)


class DocumentTypes:
    RIB = "RIB"
    """French bank account identification document"""

    STATEMENT = "statement"
    """Bank statement"""

    CONTRACT = "contract"
    """Contract between organisation and subscriber"""

    CERTIFICATE = "certificate"
    """Certificate from the organisation to the subscriber"""

    NOTICE = "notice"
    """Notice from the organisation to the subscriber"""

    REPORT = "report"
    """Informative report"""

    BILL = "bill"
    """Bill"""

    KIID = "kiid"
    """Key Investor Information Document"""

    IDENTITY = "identity"
    """Identity document (e.g. national identity card, driving licence, etc...)"""

    PAYSLIP = "payslip"
    """Payslip"""

    OTHER = "other"


class Detail(BaseObject, Currency):
    """
    Detail of a subscription
    """

    label = StringField("label of the detail line")
    infos = StringField("information")
    datetime = DateField("date information")
    price = DecimalField("Total price, taxes included")
    vat = DecimalField("Value added Tax")
    currency = StringField("Currency", default=None)
    quantity = DecimalField("Number of units consumed")
    unit = StringField("Unit of the consumption")


class Document(BaseObject):
    """
    Document.
    """

    date = DateField("The day the document has been sent to the subscriber")
    format = StringField("file format of the document")
    label = StringField("label of document")
    type = StringField("type of document")
    transactions = Field("List of transaction ID related to the document", list, default=[])
    has_file = BoolField("Boolean to set if file is available", default=True)
    number = StringField("Number of the document (if present and meaningful for user)")

    def __repr__(self):
        return f"<{type(self).__name__} id={self.id!r} label={self.label!r} date={self.date!r}>"


class Bill(Document, Currency):
    """
    Bill.
    """

    total_price = DecimalField("Price to pay")
    currency = StringField("Currency", default=None)
    vat = DecimalField("VAT included in the price")
    pre_tax_price = DecimalField("Price without the VAT or other taxes")
    duedate = DateField("The day the bill must be paid")
    startdate = DateField("The first day the bill applies to")
    finishdate = DateField("The last day the bill applies to")

    def __repr__(self):
        return "<{} id={!r} label={!r} date={!r} total_price={!r}>".format(
            type(self).__name__,
            self.id,
            self.label,
            self.date,
            self.total_price,
        )

    # compatibility properties
    @property
    def price(self):
        warnings.warn(
            'Field "price" is deprecated, use "total_price" field instead.',
            DeprecatedFieldWarning,
            stacklevel=3,
        )

        if empty(self.total_price):
            return self.total_price
        return abs(self.total_price)

    @price.setter
    def price(self, value):
        warnings.warn(
            'Field "price" is deprecated, use "total_price" field instead.',
            DeprecatedFieldWarning,
            stacklevel=3,
        )
        if empty(value):
            self.total_price = value
            self._income = None
            return

        value = abs(value)
        if not self.income:
            self.total_price = value
        else:
            self.total_price = -value
        self._income = None

    @property
    def income(self):
        warnings.warn(
            'Field "income" is deprecated, use "total_price" field instead.',
            DeprecatedFieldWarning,
            stacklevel=3,
        )

        if empty(self.total_price):
            return self._income or False
        return self.total_price <= 0

    @income.setter
    def income(self, value):
        warnings.warn(
            'Field "income" is deprecated, use "total_price" field instead.',
            DeprecatedFieldWarning,
            stacklevel=3,
        )

        if empty(self.total_price):
            self._income = value
        else:
            # the price was set before income
            # we can avoid handling the obnoxious _income field
            income_sign = {True: -1, False: 1}
            self.total_price = abs(self.total_price) * income_sign[value]
            self._income = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.type = DocumentTypes.BILL
        self._income = None
        # _income shall be set to True or False only if income is set *before* price property
        # in all other cases, we can map income property depending on total_price


class Subscription(BaseObject):
    """
    Subscription to a service.
    """

    label = StringField("label of subscription")
    subscriber = StringField("Subscriber name or identifier (for companies)")
    validity = DateField("End validity date of the subscription (if any)")
    renewdate = DateField("Reset date of consumption, for time based subscription (monthly, yearly, etc)")
    is_fake = BoolField("Set True if website doesn't provide a real subscription", default=False)

    def __repr__(self):
        return f"<{type(self).__name__} id={self.id!r} label={self.label!r}>"


class CapDocument(CapCollection, CapCredentialsCheck):
    accepted_document_types = ()
    """
    Tuple of document types handled by the module (:class:`DocumentTypes`)
    """

    def check_credentials(self) -> bool:
        """
        Check that the given credentials are correct by trying to login.

        The default implementation of this method check if the class using this capability
        has a browser, execute its do_login if it has one and then see if no error pertaining to the creds is raised.
        If any other unexpected error occurs, we don't know whether the creds are correct or not.
        """
        if getattr(self, "BROWSER", None) is None:
            raise NotImplementedError()

        try:
            self.browser.do_login()
        except BrowserIncorrectPassword:
            return False

        return True

    def iter_subscription(self):
        """
        Iter subscriptions.

        :rtype: iter[:class:`Subscription`]
        """
        raise NotImplementedError()

    def get_subscription(self, _id):
        """
        Get a subscription.

        :param _id: ID of subscription
        :rtype: :class:`Subscription`
        :raises: :class:`SubscriptionNotFound`
        """
        return find_object(
            self.iter_subscription(),
            id=_id,
            error=SubscriptionNotFound,
        )

    def iter_documents_history(self, subscription):
        """
        Iter history of a subscription.

        :param subscription: subscription to get history
        :type subscription: :class:`Subscription`
        :rtype: iter[:class:`Detail`]
        """
        raise NotImplementedError()

    def iter_bills_history(self, subscription):
        """
        Iter history of a subscription.

        :param subscription: subscription to get history
        :type subscription: :class:`Subscription`
        :rtype: iter[:class:`Detail`]
        """
        return self.iter_documents_history(subscription)

    def get_document(self, id):
        """
        Get a document.

        :param id: ID of document
        :rtype: :class:`Document`
        :raises: :class:`DocumentNotFound`
        """
        raise NotImplementedError()

    def download_document(self, id):
        """
        Download a document.

        :param id: ID of document
        :rtype: bytes
        :raises: :class:`DocumentNotFound`
        """
        raise NotImplementedError()

    def download_document_pdf(self, id):
        """
        Download a document, convert it to PDF if it isn't the document format.

        :param id: ID of document
        :rtype: bytes
        :raises: :class:`DocumentNotFound`
        """
        if not isinstance(id, Document):
            id = self.get_document(id)

        if id.format == "pdf":
            return self.download_document(id)
        else:
            raise NotImplementedError()

    def iter_documents(self, subscription):
        """
        Iter documents.

        :param subscription: subscription to get documents
        :type subscription: :class:`Subscription`
        :rtype: iter[:class:`Document`]
        """
        raise NotImplementedError()

    def iter_documents_by_types(self, subscription, accepted_types):
        """
        Iter documents with specific types.

        :param subscription: subscription to get documents
        :type subscription: :class:`Subscription`
        :param accepted_types: list of document types that should be returned
        :type accepted_types: [:class:`DocumentTypes`]
        :rtype: iter[:class:`Document`]
        """
        accepted_types = frozenset(accepted_types)
        for document in self.iter_documents(subscription):
            if document.type in accepted_types:
                yield document

    def iter_bills(self, subscription):
        """
        Iter bills.

        :param subscription: subscription to get bills
        :type subscription: :class:`Subscription`
        :rtype: iter[:class:`Bill`]
        """
        documents = self.iter_documents(subscription)
        return [doc for doc in documents if doc.type == "bill"]

    def get_details(self, subscription):
        """
        Get details of a subscription.

        :param subscription: subscription to get details
        :type subscription: :class:`Subscription`
        :rtype: iter[:class:`Detail`]
        """
        raise NotImplementedError()

    def get_balance(self, subscription):
        """
        Get the balance of a subscription.

        :param subscription: subscription to get balance
        :type subscription: :class:`Subscription`
        :rtype: class:`Detail`
        """
        raise NotImplementedError()

    def iter_resources(self, objs, split_path):
        """
        Iter resources. Will return :func:`iter_subscriptions`.
        """
        if Subscription in objs:
            self._restrict_level(split_path)
            return self.iter_subscription()


class CapDocumentMatching(CapDocument):
    """
    Capability for matching data between synchronizations.

    This is mostly useful for providers which have to compare states across time.
    For example, a provider has to compare subscriptions freshly returned to subscriptions
    returned in a previous sync.
    """

    def match_subscription(self, subscription, old_subscriptions):
        """Search a subscription in `old_subscriptions` corresponding to `subscription`.

        `old_subscriptions` is a list of subscriptions found in a previous
        synchronisation.
        However, they may not be the exact same objects but only reconstructed
        objects with the same data, although even it could be partial.
        For example, they may have been marshalled, sometimes loosely, thus some
        attributes may be missing (like `_private` attributes) or unset (some
        providers may choose not to even save all attributes).
        Also, `old_subscriptions` may not contain all subscriptions from previous state,
        but only subscriptions which have not been matched yet.

        :param subscription: fresh subscription to search for
        :type subscription: :class:`Subscription`
        :param old_subscriptions: candidates subscriptions from previous sync
        :type old_subscriptions: iter[:class:`Subscription`]
        :return: the corresponding subscription from `old_subscriptions`, or `None` if none matches
        :rtype: :class:`Subscription`
        """

        raise NotImplementedError()

    def match_document(self, document, old_documents):
        """
        :param document: fresh document to search for
        :type document: :class:`Bill`
        :param old_documents: candidates documents from previous sync
        :type old_documents: iter[:class:`Bill`]
        :return: the corresponding document from `old_documents`, or `None` if none matches
        :rtype: :class:`Bill`
        """

        raise NotImplementedError()


class DocumentCategory(Enum):
    """
    used for categories attribute in module.py
    use OTHER category sparingly,
    prefer define a new value if you really need a new one
    respect uppercase for enum and titled for string

    tag Module with it:
    example:
        document_categories = {DocumentCategory.ENERGY}
    """

    ADMINISTRATIVE = "Administrative"
    BANK_INSURANCE = "Bank & Insurance"
    BILLING = "Billing"
    BUSINESS = "Business"
    ENERGY = "Energy Provider"
    FOOD = "Food"
    INTERNET_TELEPHONY = "Internet & Telephony"
    LEISURE = "Leisure"
    OTHER = "Other"
    REAL_ESTATE = "Real estate"
    SAFE_DEPOSIT_BOX = "Safe deposit box"
    """The Safe Deposit Box category concerns website whose purpose is to store
    official and sensitive documents such as proof of identity or wage slip,
    filed by companies and other legal entities."""
    SHOPPING = "Shopping"
    SOFTWARE = "Software"
    TAX = "Tax"
    TOURISM = "Tourism"
    TRANSPORT = "Transport"
    WATER = "Water Provider"
