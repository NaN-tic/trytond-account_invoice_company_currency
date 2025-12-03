# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal

from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.modules.currency.fields import Monetary


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
        super().__setup__()
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
        Currency = Pool().get('currency.currency')

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
                'aml.account IN (SELECT account '
                    'FROM account_invoice_tax WHERE invoice = %s)' % values)

        if cursor:
            cursor.execute(query)

        for _, value in cursor.fetchall():
            # compute currency company in case currency has not digits
            totals += Currency.compute(self.company.currency, Decimal(value),
                self.company.currency, round=True)

        return totals

    @classmethod
    def get_amount(cls, invoices, names):
        pool = Pool()
        Currency = pool.get('currency.currency')

        new_names = [n for n in names if not n.startswith('company_')]
        for fname in ('untaxed_amount', 'tax_amount', 'total_amount'):
            if 'company_%s' % fname in names and fname not in new_names:
                new_names.append(fname)
        result = super().get_amount(invoices, new_names)

        company_names = [n for n in names if n.startswith('company_')]
        if company_names:
            for invoice in invoices:
                for fname in company_names:
                    value = getattr(invoice, '%s_cache' % fname)
                    if value is None:
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
    def draft(cls, invoices):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        InvoiceTax = pool.get('account.invoice.tax')

        to_write = [invoices, {
                'company_untaxed_amount_cache': None,
                'company_tax_amount_cache': None,
                'company_total_amount_cache': None,
                }]
        cls.write(*to_write)

        line_to_write = []
        tax_to_write = []
        for invoice in invoices:
            line_to_write += list(invoice.lines or [])
            tax_to_write += list(invoice.taxes or [])

        super().draft(invoices)

        InvoiceLine.write(line_to_write, {
            'company_amount_cache': None,
            })
        InvoiceTax.write(tax_to_write, {
            'company_base_cache': None,
            'company_amount_cache': None,
            })

    @classmethod
    def copy(cls, invoices, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['company_untaxed_amount_cache'] = None
        default['company_tax_amount_cache'] = None
        default['company_total_amount_cache'] = None
        return super().copy(invoices, default=default)

    @classmethod
    def _store_cache(cls, invoices):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        InvoiceTax = pool.get('account.invoice.tax')

        line_to_write = []
        tax_to_write = []
        for invoice in invoices:
            if (invoice.company_untaxed_amount == invoice.company_untaxed_amount_cache
                    and invoice.company_tax_amount == invoice.company_tax_amount_cache
                    and invoice.company_total_amount == invoice.company_total_amount_cache):
                continue
            invoice.company_untaxed_amount_cache = invoice.company_untaxed_amount
            invoice.company_tax_amount_cache = invoice.company_tax_amount
            invoice.company_total_amount_cache = invoice.company_total_amount

            for line in invoice.lines:
                line_to_write.extend(([line], {
                    'company_amount_cache': line.company_amount,
                    }))

            for line in invoice.taxes:
                tax_to_write.extend(([line], {
                    'company_base_cache': line.company_base,
                    'company_amount_cache': line.company_amount,
                    }))

        super()._store_cache(invoices)

        if line_to_write:
            InvoiceLine.write(*line_to_write)

        if tax_to_write:
            InvoiceTax.write(*tax_to_write)


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
    company_base_cache = Monetary('Base (Company Currency)',
        digits='company_currency', currency='company_currency', readonly=True)
    company_amount = fields.Function(Monetary('Amount (Company Currency)',
        currency='company_currency', digits='company_currency',
        states={
            'invisible': ~Eval('_parent_invoice',
                    {}).get('different_currencies', False),
        }), 'get_amount')
    company_amount_cache = Monetary('Amount (Company Currency)',
        digits='company_currency', currency='company_currency', readonly=True)

    @classmethod
    def copy(cls, taxes, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['company_base_cache'] = None
        default['company_amount_cache'] = None
        return super().copy(taxes, default=default)

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
                value = getattr(invoice_tax, '%s_cache' % fname)
                if value is None:
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
    company_amount_cache = Monetary('Amount (Company Currency)',
        digits='company_currency', currency='company_currency', readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        extra_excludes = {'company_amount_cache'}
        cls._check_modify_exclude |= extra_excludes

    @classmethod
    def copy(cls, lines, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['company_amount_cache'] = None
        return super().copy(lines, default=default)

    @fields.depends('invoice', 'currency', '_parent_invoice.company')
    def on_change_with_company_currency(self, name=None):
        if self.invoice and self.invoice.company.currency:
            return self.invoice.company.currency.id
        elif self.currency:
            return self.currency.id

    def get_company_amount(self, name=None):
        pool = Pool()
        Date = pool.get('ir.date')
        Currency = pool.get('currency.currency')

        currency = self.invoice and self.invoice.currency or self.currency
        currency_date = self.invoice and self.invoice.currency_date or Date.today()
        company = self.invoice and self.invoice.company or self.company

        if currency == company.currency:
            return self.amount

        if self.company_amount_cache is not None:
            return self.company_amount_cache

        with Transaction().set_context(date=currency_date):
            return Currency.compute(currency, self.amount, company.currency,
                round=True)
