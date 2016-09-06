# -*- coding: utf-8 -*-

from datetime import date
from openerp import models, fields, api
from openerp.exceptions import except_orm


class checkout_wizard(models.TransientModel):
    '''月末结账的向导'''
    _name = 'checkout.wizard'

    period_id = fields.Many2one('finance.period', u'结账会计期间')
    date = fields.Date(u'生成凭证日期', required=True)

    @api.multi
    @api.onchange('date')
    def onchange_period_id(self):
        self.period_id = self.env['finance.period'].with_context(module_name='checkout_wizard').get_period(self.date)

    @api.multi
    def button_checkout(self):
        if self.period_id:
            last_period = self.env['create.trial.balance.wizard'].compute_last_period_id(self.period_id)
            if last_period:
                if not last_period.is_closed:
                    raise except_orm(u'错误', u'上一个期间%s未结账' % last_period.name)
            if self.period_id.is_closed:
                raise except_orm(u'错误', u'本期间已结账')
            else:
                voucher_obj = self.env['voucher']
                voucher_ids = voucher_obj.search([('period_id', '=', self.period_id.id)])
                i = 0
                for voucher_id in voucher_ids:
                    if voucher_id.state != 'done':
                        i += 1
                if i != 0:
                    raise except_orm(u'错误', u'该期间有%s张凭证未审核' % i)
                else:
                    voucher_line = []  # 生成的结账凭证行
                    account_obj = self.env['finance.account']
                    company_obj = self.env['res.company']
                    voucher_line_obj = self.env['voucher.line']
                    revenue_account_ids = account_obj.search([('costs_types', '=', 'in')])  # 收入类科目
                    expense_account_ids = account_obj.search([('costs_types', '=', 'out')])  # 费用类科目
                    revenue_total = 0  # 收入类科目合计
                    expense_total = 0  # 费用类科目合计
                    for revenue_account_id in revenue_account_ids:
                        voucher_line_ids = voucher_line_obj.search([
                            ('account_id', '=', revenue_account_id.id),
                            ('voucher_id.period_id', '=', self.period_id.id)])
                        credit_total = 0
                        for voucher_line_id in voucher_line_ids:
                            credit_total += voucher_line_id.credit
                        revenue_total += credit_total
                        if credit_total != 0:
                            res = {
                                'name': u'月末结账',
                                'account_id': revenue_account_id.id,
                                'debit': credit_total,
                                'credit': 0,
                            }
                            voucher_line.append(res)
                    for expense_account_id in expense_account_ids:
                        voucher_line_ids = voucher_line_obj.search([
                            ('account_id', '=', expense_account_id.id),
                            ('voucher_id.period_id', '=', self.period_id.id)])
                        debit_total = 0
                        for voucher_line_id in voucher_line_ids:
                            debit_total += voucher_line_id.debit
                        expense_total += debit_total
                        if debit_total != 0:
                            res = {
                                'name': u'月末结账',
                                'account_id': expense_account_id.id,
                                'debit': 0,
                                'credit': debit_total,
                            }
                            voucher_line.append(res)
                    # 利润结余
                    year_profit_account = company_obj.search([])[0].profit_account
                    remain_account = company_obj.search([])[0].remain_account
                    if not year_profit_account:
                        raise except_orm(u'错误', u'公司本年利润科目未配置')
                    if not remain_account:
                        raise except_orm(u'错误', u'公司未分配利润科目未配置')
                    if (revenue_total - expense_total) > 0:
                        res = {
                            'name': u'利润结余',
                            'account_id': year_profit_account.id,
                            'debit': 0,
                            'credit': revenue_total - expense_total,
                        }
                        voucher_line.append(res)
                    if (revenue_total - expense_total) < 0:
                        res = {
                            'name': u'利润结余',
                            'account_id': year_profit_account.id,
                            'debit': expense_total - revenue_total,
                            'credit': 0,
                        }
                        voucher_line.append(res)
                    # 生成凭证
                    if voucher_line:
                        valus = {
                            'is_checkout': True,
                            'date': self.date,
                            'line_ids': [
                                (0, 0, line) for line in voucher_line],
                        }
                        voucher = voucher_obj.create(valus)
                        voucher.voucher_done()
                year_account = None
                if self.period_id.month == '12':
                    year_profit_ids = voucher_line_obj.search([
                        ('account_id', '=', year_profit_account.id),
                        ('voucher_id.period_id', '=', self.period_id.id)])
                    year_total = 0
                    for year_profit_id in year_profit_ids:
                        year_total += (year_profit_id.credit - year_profit_id.debit)
                    precision = self.env['decimal.precision'].precision_get('Account')
                    year_total = round(year_total, precision)
                    if year_total != 0:
                        year_line_ids = [{
                            'name': u'年度结余',
                            'account_id': remain_account.id,
                            'debit': 0,
                            'credit': year_total,
                        }, {
                            'name': u'年度结余',
                            'account_id': year_profit_account.id,
                            'debit': year_total,
                            'credit': 0,
                        }]
                        value = {'is_checkout': True,
                                 'date': self.date,
                                 'line_ids': [
                                     (0, 0, line) for line in year_line_ids],
                                 }
                        year_account = voucher_obj.create(value)  # 创建结转凭证
                        year_account.voucher_done()  # 凭证审核
                # 生成科目余额表
                trial_wizard = self.env['create.trial.balance.wizard'].create({
                    'period_id': self.period_id.id,
                })
                trial_wizard.create_trial_balance()
                # 按用户设置重排结账会计期间凭证号（会计要求凭证号必须连续）
                self.recreate_voucher_name(self.period_id)
                # 关闭会计期间
                self.period_id.is_closed = True
                # 如果下一个会计期间没有，则创建。
                next_period = self.env['create.trial.balance.wizard'].compute_next_period_id(self.period_id)
                if not next_period:
                    if self.period_id.month == '12':
                        self.env['finance.period'].create({'year': str(int(self.period_id.year) + 1),
                                                           'month': '1',})
                    else:
                        self.env['finance.period'].create({'year': self.period_id.year,
                                                           'month': str(int(self.period_id.month) + 1),})
                # 显示凭证
                view = self.env.ref('finance.voucher_form')
                if voucher_line or year_account:
                    # 因重置凭证号，查找最后一张结转凭证
                    voucher = self.env['voucher'].search(
                        [('is_checkout', '=', True), ('period_id', '=', self.period_id.id)], order="create_date desc",
                        limit=1)
                    return {
                        'name': u'月末结账',
                        'view_type': 'form',
                        'view_mode': 'form',
                        'views': [(view.id, 'form')],
                        'res_model': 'voucher',
                        'type': 'ir.actions.act_window',
                        'res_id': voucher.id,
                        'limit': 300,
                    }

    # 反结账
    @api.multi
    def button_counter_checkout(self):
        if self.period_id:
            if not self.period_id.is_closed:
                raise except_orm(u'错误', u'本期间未结账')
            else:
                next_period = self.env['create.trial.balance.wizard'].compute_next_period_id(self.period_id)
                if next_period:
                    if next_period.is_closed:
                        raise except_orm(u'错误', u'下一个期间%s已结账！' % next_period.name)
                self.period_id.is_closed = False
                voucher_ids = self.env['voucher'].search([('is_checkout', '=', True),
                                                          ('period_id', '=', self.period_id.id)])
                for voucher_id in voucher_ids:
                    voucher_id.voucher_draft()
                    voucher_id.unlink()
                trial_balance_objs = self.env['trial.balance'].search([('period_id', '=', self.period_id.id)])
                trial_balance_objs.unlink()

    # 按用户设置重排结账会计期间凭证号（会计要求凭证号必须连续）
    @api.multi
    def recreate_voucher_name(self, period_id):
        # 取重排凭证设置
        # 是否重置凭证号
        auto_reset = self.env['ir.values'].get_default('finance.config.settings', 'default_auto_reset')
        # 重置凭证间隔:年  月
        reset_period = self.env['ir.values'].get_default('finance.config.settings', 'default_reset_period')
        # 重置后起始数字
        reset_init_number = self.env['ir.values'].get_default('finance.config.settings', 'default_reset_init_number')
        if auto_reset is True:
            # 取ir.sequence中的会计凭证的参数
            force_company = self._context.get('force_company')
            if not force_company:
                force_company = self.env.user.company_id.id
            company_ids = self.env['res.company'].search([]).ids + [False]
            seq_ids = self.env['ir.sequence'].search(['&', ('code', '=', 'voucher'), ('company_id', 'in', company_ids)])
            preferred_sequences = [s for s in seq_ids if s.company_id and s.company_id.id == force_company]
            seq_id = preferred_sequences[0] if preferred_sequences else seq_ids[0]
            voucher_obj = self.env['voucher']
            # 按年重置
            last_period = self.env['create.trial.balance.wizard'].compute_last_period_id(self.period_id)
            if reset_period == 'year':
                if last_period:
                    if period_id.year != last_period.year:
                        # 按年，而且是第一个会计期间
                        last_voucher_number = reset_init_number
                    else:
                        # 查找上一期间最后凭证号
                        last_period_voucher_name = voucher_obj.search([('period_id', '=', last_period.id)],
                                                                      order="create_date desc", limit=1).name
                        # 凭证号转换为数字
                        last_voucher_number = int(filter(str.isdigit, last_period_voucher_name.encode("utf-8"))) + 1
                else:
                    last_voucher_number = reset_init_number
                # 产生凭证号前后缀
                d = self.env['ir.sequence']._interpolation_dict_context(context=self._context)
                try:
                    interpolated_prefix = self.env['ir.sequence']._interpolate(seq_id.prefix, d)
                    interpolated_suffix = self.env['ir.sequence']._interpolate(seq_id.suffix, d)
                except ValueError:
                    raise except_orm(_(u'警告'),
                                     _(u'无效的前缀或后缀 \'%s\'') % (seq_id.name))
                voucher_ids = voucher_obj.search([('period_id', '=', period_id.id)], order='create_date')
                for voucher_id in voucher_ids:
                    # 产生凭证号
                    next_voucher_name = interpolated_prefix + '%%0%sd' % seq_id.padding % last_voucher_number + interpolated_suffix
                    last_voucher_number += 1
                    # 更新凭证号
                    voucher_id.write({'name': next_voucher_name})
            # 按月重置
            else:
                last_voucher_number = reset_init_number
                # 产生凭证号前后缀
                d = self.env['ir.sequence']._interpolation_dict_context(context=self._context)
                try:
                    interpolated_prefix = self.env['ir.sequence']._interpolate(seq_id.prefix, d)
                    interpolated_suffix = self.env['ir.sequence']._interpolate(seq_id.suffix, d)
                except ValueError:
                    raise except_orm(_(u'警告'),
                                     _(u'无效的前缀或后缀 \'%s\'') % (seq_id.name))
                voucher_ids = voucher_obj.search([('period_id', '=', period_id.id)], order='create_date')
                for voucher_id in voucher_ids:
                    # 产生凭证号
                    next_voucher_name = interpolated_prefix + '%%0%sd' % seq_id.padding % last_voucher_number + interpolated_suffix
                    # 更新凭证号
                    voucher_id.write({'name': next_voucher_name})
                    last_voucher_number += 1
            # update ir.sequence  number_next
            if last_voucher_number:
                self.env.cr.execute("UPDATE ir_sequence SET suffix=%s WHERE id=%s ",
                                    (seq_id.suffix or interpolated_suffix, seq_id.id))
                self.env['ir.sequence']._alter_sequence(seq_id.id, seq_id.number_increment,
                                                        seq_id.number_next)
                self.env.cr.commit()
