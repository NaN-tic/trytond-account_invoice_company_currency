import datetime
import unittest
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from proteus import Model
from trytond.modules.account.tests.tools import (create_chart,
                                                 create_fiscalyear, create_tax,
                                                 create_tax_code, get_accounts)
from trytond.modules.account_invoice.tests.tools import \
    set_fiscalyear_invoice_sequences
from trytond.modules.company.tests.tools import create_company, get_company
from trytond.modules.currency.tests.tools import get_currency
from trytond.tests.test_tryton import drop_db
from trytond.tests.tools import activate_modules


class Test(unittest.TestCase):

    def setUp(self):
        drop_db()
        super().setUp()

    def tearDown(self):
        drop_db()
        super().tearDown()

    def test(self):

        # Imports
        today = datetime.date.today()

        # Install account_invoice_company_currency
        activate_modules('account_invoice_company_currency')

        # Create currencies
        usd = get_currency(code='USD')
        eur = get_currency(code='EUR')

        # Create company
        _ = create_company()
        company = get_company()

        # Create fiscal year
        fiscalyear = set_fiscalyear_invoice_sequences(
            create_fiscalyear(company))
        fiscalyear.click('create_period')
        Period = Model.get('account.period')
        period, = Period.find([
            ('start_date', '>=', today.replace(day=1)),
            ('end_date', '<=', today.replace(day=1) + relativedelta(months=+1)),
        ],
                              limit=1)

        # Create chart of accounts
        _ = create_chart(company)
        accounts = get_accounts(company)
        revenue = accounts['revenue']
        expense = accounts['expense']

        # Create tax
        tax = create_tax(Decimal('.10'))
        tax.save()
        invoice_base_code = create_tax_code(tax, 'base', 'invoice')
        invoice_base_code.save()
        invoice_tax_code = create_tax_code(tax, 'tax', 'invoice')
        invoice_tax_code.save()
        credit_note_base_code = create_tax_code(tax, 'base', 'credit')
        credit_note_base_code.save()
        credit_note_tax_code = create_tax_code(tax, 'tax', 'credit')
        credit_note_tax_code.save()

        # Create party
        Party = Model.get('party.party')
        party = Party(name='Party')
        party.save()

        # Create account category
        ProductCategory = Model.get('product.category')
        account_category = ProductCategory(name="Account Category")
        account_category.accounting = True
        account_category.account_expense = expense
        account_category.account_revenue = revenue
        account_category.customer_taxes.append(tax)
        account_category.save()

        # Create product
        ProductUom = Model.get('product.uom')
        unit, = ProductUom.find([('name', '=', 'Unit')])
        ProductTemplate = Model.get('product.template')
        template = ProductTemplate()
        template.name = 'product'
        template.default_uom = unit
        template.type = 'service'
        template.list_price = Decimal('40')
        template.account_category = account_category
        template.save()
        product, = template.products

        # Create payment term
        PaymentTerm = Model.get('account.invoice.payment_term')
        payment_term = PaymentTerm(name='Term')
        line = payment_term.lines.new(type='percent', ratio=Decimal('.5'))
        delta, = line.relativedeltas
        delta.days = 20
        line = payment_term.lines.new(type='remainder')
        delta = line.relativedeltas.new(days=40)
        payment_term.save()

        # Create invoice with company currency
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = party
        invoice.payment_term = payment_term
        invoice.currency = usd
        line = invoice.lines.new()
        line.product = product
        line.quantity = 5
        line.unit_price = Decimal('40.00')
        invoice.save()
        line1 = invoice.lines[0]
        self.assertEqual(line1.amount, Decimal('200.00'))
        self.assertEqual(line1.company_amount, Decimal('200.00'))
        line = invoice.lines.new()
        line.account = revenue
        line.description = 'Test'
        line.quantity = 1
        line.unit_price = Decimal('20.00')
        invoice.save()

        for line in invoice.lines:
            if line != line1:
                line2 = line
                break

        self.assertEqual(line2.amount, Decimal('20.00'))
        self.assertEqual(line2.company_amount, Decimal('20.00'))
        self.assertEqual(invoice.untaxed_amount, Decimal('220.00'))
        self.assertEqual(invoice.tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.total_amount, Decimal('240.00'))
        self.assertEqual(invoice.company_untaxed_amount, Decimal('220.00'))
        self.assertEqual(invoice.company_tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.company_total_amount, Decimal('240.00'))

        invoice.click('post')

        self.assertEqual(invoice.different_currencies, False)
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.untaxed_amount, Decimal('220.00'))
        self.assertEqual(invoice.tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.total_amount, Decimal('240.00'))
        self.assertEqual(invoice.company_untaxed_amount, Decimal('220.00'))
        self.assertEqual(invoice.company_tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.company_total_amount, Decimal('240.00'))

        # Create invoice with alternate currency
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = party
        invoice.payment_term = payment_term
        invoice.currency = eur
        line = invoice.lines.new()
        line.product = product
        line.quantity = 5
        line.unit_price = Decimal('40.00')
        invoice.save()
        line1 = invoice.lines[0]
        self.assertEqual(line.amount, Decimal('200.00'))
        self.assertEqual(line1.company_amount, Decimal('100.00'))
        line = invoice.lines.new()
        line.account = revenue
        line.description = 'Test'
        line.quantity = 1
        line.unit_price = Decimal(20)
        invoice.save()

        for line in invoice.lines:
            if line != line1:
                line2 = line
                break

        self.assertEqual(line2.amount, Decimal('20.00'))
        self.assertEqual(line2.company_amount, Decimal('10.00'))
        self.assertEqual(invoice.untaxed_amount, Decimal('220.00'))
        self.assertEqual(invoice.tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.total_amount, Decimal('240.00'))
        self.assertEqual(invoice.company_untaxed_amount, Decimal('110.00'))
        self.assertEqual(invoice.company_tax_amount, Decimal('10.00'))
        self.assertEqual(invoice.company_total_amount, Decimal('120.00'))

        invoice.click('post')

        self.assertEqual(invoice.different_currencies, True)
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.untaxed_amount, Decimal('220.00'))
        self.assertEqual(invoice.tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.total_amount, Decimal('240.00'))
        self.assertEqual(invoice.company_untaxed_amount, Decimal('110.00'))
        self.assertEqual(invoice.company_tax_amount, Decimal('10.00'))
        self.assertEqual(invoice.company_total_amount, Decimal('120.00'))
