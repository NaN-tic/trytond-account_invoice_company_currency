# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal

from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.modules.currency.fields import Monetary

__all__ = ['Invoice', 'InvoiceTax', 'InvoiceLine']


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    different_currencies = fields.Function(
        fields.Boolean('Different Currencies'),
        'on_change_with_different_currencies')
    company_currency = fields.Function(
        fields.Many2One('currency.currency', 'Company Currency'),
        'on_change_with_company_currency')
    company_untaxed_amount_cache = Monetary('Untaxed (Company Currency)',
        digits='company_currency', currency='company_currency', readonly=True)
    company_untaxed_amount = fields.Function(Monetary('Untaxed (Company Currency)',
        digits='company_currency', currency='company_currency', states={
            'invisible': ~Eval('different_currencies', False),
        }), 'get_amount')
    company_tax_amount_cache = Monetary('Tax (Company Currency)',
        digits='company_currency', currency='company_currency', readonly=True)
    company_tax_amount = fields.Function(Monetary('Tax (Company Currency)',
        digits='company_currency', currency='company_currency', states={
            'invisible': ~Eval('different_currencies', False),
        }), 'get_amount')
    company_total_amount_cache = Monetary('Total (Company Currency)',
        digits='company_currency', currency='company_currency', readonly=True)
    company_total_amount = fields.Function(Monetary('Total (Company Currency)',
        digits='company_currency', currency='company_currency', states={
            'invisible': ~Eval('different_currencies', False),
            }), 'get_amount')

    @classmethod
    def __setup__(cls):
        super(Invoice, cls).__setup__()
        extra_excludes = {'company_total_amount_cache',
            'company_tax_amount_cache', 'company_untaxed_amount_cache'}
        cls._check_modify_exclude |= extra_excludes

    @fields.depends('company', 'currency')
    def on_change_with_different_currencies(self, name=None):
        if self.company:
            return self.company.currency != self.currency
        return False

    @fields.depends('company')
    def on_change_with_company_currency(self, name=None):
        if self.company and self.company.currency:
            return self.company.currency.id

    def get_company_quantities(self, fname):
        cursor = Transaction().connection.cursor()

        totals = 0
        if fname == 'total_amount':
            if self.type == 'out':
                values = ('aml.debit - aml.credit ', self.id)
            else:
                values = ('aml.credit - aml.debit ', self.id)

            query = ('SELECT ai.id, '
                'CASE WHEN aml.account = ai.account '
                    'THEN %s'
                    'ELSE 0 '
                    'END AS total_amount '
                'FROM account_invoice AS ai '
                    'JOIN account_move AS am ON ai.move = am.id '
                    'JOIN account_move_line AS aml ON aml.move = am.id '
                'WHERE ai.id =%s' % values)

        elif fname == 'untaxed_amount':
            if self.type == 'out':
                values = ('aml.credit - aml.debit ', self.id, self.id)
            else:
                values = ('aml.debit - aml.credit ', self.id, self.id)

            query = ('SELECT ai.id, %s AS untaxed_amount '
                'FROM account_invoice AS ai '
                    'JOIN account_move AS am ON ai.move = am.id '
                    'JOIN account_move_line AS aml ON aml.move = am.id '
                'WHERE ai.id =%s AND aml.account IN ('
                    'SELECT account '
                    'FROM account_invoice_line WHERE invoice = %s)' % values)

        elif fname == 'tax_amount':
            if self.type == 'out':
                values = ('aml.credit - aml.debit ', self.id, self.id)
            else:
                values = ('aml.debit - aml.credit ', self.id, self.id)

            query = ('SELECT ai.id, %s AS tax_amount '
                'FROM account_invoice AS ai '
                    'JOIN account_move AS am ON ai.move = am.id '
                    'JOIN account_move_line AS aml ON aml.move = am.id '
                'WHERE ai.id =%s AND aml.account != ai.account AND '
                'aml.account NOT IN (SELECT account '
                    'FROM account_invoice_line WHERE invoice = %s)' % values)
        if cursor:
            cursor.execute(query)

        for _, value in cursor.fetchall():
            totals += self.currency.round(Decimal(value))

        return totals

    @classmethod
    def get_amount(cls, invoices, names):
        pool = Pool()
        Currency = pool.get('currency.currency')

        new_names = [n for n in names if not n.startswith('company_')]
        for fname in ('untaxed_amount', 'tax_amount', 'total_amount'):
            if 'company_%s' % fname in names and fname not in new_names:
                new_names.append(fname)
        result = super(Invoice, cls).get_amount(invoices, new_names)

        company_names = [n for n in names if n.startswith('company_')]
        if company_names:
            for invoice in invoices:
                for fname in company_names:
                    if getattr(invoice, '%s_cache' % fname):
                        value = getattr(invoice, '%s_cache' % fname)
                    else:
                        if invoice.move:
                            value = invoice.get_company_quantities(fname.replace('company_', ''))
                        else:
                            with Transaction().set_context(
                                    date=invoice.currency_date):
                                value = Currency.compute(invoice.currency,
                                    result[fname[8:]][invoice.id],
                                    invoice.company.currency, round=True)
                    result.setdefault(fname, {})[invoice.id] = value
        for key in list(result.keys()):
            if key not in names:
                del result[key]
        return result

    @classmethod
    def validate_invoice(cls, invoices):
        to_write = []
        for invoice in invoices:
            if invoice.type == 'in':
                values = cls._save_company_currency_amounts(invoice)
                to_write.extend(([invoice], values))
        if to_write:
            cls.write(*to_write)
        super(Invoice, cls).validate_invoice(invoices)

    @classmethod
    def post(cls, invoices):
        super(Invoice, cls).post(invoices)
        # Save amounts after posting as their computation is faster
        to_write = []
        for invoice in invoices:
            values = cls._save_company_currency_amounts(invoice)
            to_write.extend(([invoice], values))
        if to_write:
            cls.write(*to_write)

    @classmethod
    def draft(cls, invoices):
        to_write = [invoices, {
                'company_untaxed_amount_cache': None,
                'company_tax_amount_cache': None,
                'company_total_amount_cache': None,
                }]
        cls.write(*to_write)
        super(Invoice, cls).draft(invoices)

    @classmethod
    def copy(cls, invoices, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['company_untaxed_amount_cache'] = None
        default['company_tax_amount_cache'] = None
        default['company_total_amount_cache'] = None
        return super(Invoice, cls).copy(invoices, default=default)

    @classmethod
    def _save_company_currency_amounts(cls, invoice):
        pool = Pool()
        Currency = pool.get('currency.currency')

        values = {}
        if invoice.move:
            for fname in ('untaxed_amount', 'tax_amount', 'total_amount'):
                value = invoice.get_company_quantities(fname)
                values['company_%s_cache' % fname] = value
        else:
            with Transaction().set_context(date=invoice.currency_date):
                for fname in ('untaxed_amount', 'tax_amount', 'total_amount'):
                    value = Currency.compute(invoice.currency,
                        getattr(invoice, fname), invoice.company.currency,
                        round=True)
                    values['company_%s_cache' % fname] = value
        return values


class InvoiceTax(metaclass=PoolMeta):
    __name__ = 'account.invoice.tax'
    company_currency = fields.Function(fields.Many2One('currency.currency',
        'Company Currency'), 'on_change_with_company_currency')
    company_base = fields.Function(Monetary('Base (Company Currency)',
        currency='company_currency', digits='company_currency',
        states={
            'invisible': ~Eval('_parent_invoice',
                    {}).get('different_currencies', False),
        }), 'get_amount')
    company_amount = fields.Function(Monetary('Amount (Company Currency)',
        currency='company_currency', digits='company_currency',
        states={
            'invisible': ~Eval('_parent_invoice',
                    {}).get('different_currencies', False),
        }), 'get_amount')

    @fields.depends('invoice', '_parent_invoice.company')
    def on_change_with_company_currency(self, name=None):
        if self.invoice and self.invoice.company.currency:
            return self.invoice.company.currency.id

    @classmethod
    def get_amount(cls, invoice_taxes, names):
        pool = Pool()
        Currency = pool.get('currency.currency')

        result = {}
        for invoice_tax in invoice_taxes:
            for fname in names:
                with Transaction().set_context(
                        date=invoice_tax.invoice.currency_date):
                    value = Currency.compute(invoice_tax.invoice.currency,
                        getattr(invoice_tax, fname[8:]),
                        invoice_tax.invoice.company.currency, round=True)
                result.setdefault(fname, {})[invoice_tax.id] = value
        return result


class InvoiceLine(metaclass=PoolMeta):
    __name__ = 'account.invoice.line'
    company_currency = fields.Function(
        fields.Many2One('currency.currency', 'Company Currency'),
        'on_change_with_company_currency')
    company_amount = fields.Function(Monetary('Amount (Company Currency)',
        digits='company_currency', currency='company_currency'),
        'get_company_amount')

    @fields.depends('invoice', 'currency', '_parent_invoice.company')
    def on_change_with_company_currency(self, name=None):
        if self.invoice and self.invoice.company.currency:
            return self.invoice.company.currency.id
        elif self.currency:
            return self.currency.id

    def get_company_amount(self, name):
        pool = Pool()
        Date = pool.get('ir.date')
        Currency = pool.get('currency.currency')

        currency = self.invoice and self.invoice.currency or self.currency
        currency_date = self.invoice and self.invoice.currency_date or Date.today()
        company = self.invoice.company or self.company

        if currency == company.currency:
            return self.amount

        with Transaction().set_context(date=currency_date):
            return Currency.compute(currency, self.amount, company.currency,
                round=True)
