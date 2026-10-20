# 到点调度独立审查修复记录

基线150afa8；用户已批准方案A与允许路径。本记录按真实证据维护，不替代最终验收。

| 项目 | 证据/影响 | 最小修复计划 | 当前状态 |
|---|---|---|---|
| 原到点判定永不成立 | 独立contract-red：整点应1条实际0条，1failed/2.40秒/exit1 | 最近到期UTC候选与创建下界、已消费水位分离，复用原Engine | 首修42相关通过，待最终完整验证 |
| ENG-01 扫描后修改计划仍按旧到点执行 | 独立工程真实_tick/Engine探针：扫描12:00日计划后新Session改cron到13:00，或UTC改Asia/Shanghai，实际Bridge各调用1次并落success；应零执行 | 先补两正式零执行反例，再在_trigger比较当前cron/timezone/created_at与扫描计划身份，不一致本次拒绝；不新建状态或改变API | 已证实P2，待红/修复/独立重验 |

后续修复只能修改本卡允许路径，不能降低断言、权限或测试门禁。审查中间42通过不作为最终绑定；源码/测试修改后重跑受影响验证。任何新权限缺陷必须先取得真实入口证据，再记录并最小修复，不让纯猜测替代结果。

ENG-01正式红：2 failed、29 deselected、2.83秒、exit1，data/flow003-contract-eng01-red.txt，两变体均因零执行断言被真实success记录打破。

ENG-02租户身份缺口：独立工程在隔离SQLite真实_tick/Engine中证实Tenant停用而User仍active时，Bridge调用1次并落cron success；用session.execute(delete(Tenant))保留User悬空租户也同样调用。原API身份规则要求Tenant存在且active，cron新激活入口应沿用此规则。拟先补两正式零调用反例，再在_trigger新Session复核Tenant存在/active，不进入Engine，不增新角色/审批规则。首个ORM session.delete(Tenant)探针因关系置空User.tenant_id触发NOTNULL，是夹具错误，不是业务红。全部仅隔离故障注入，无真实敏感数据或生产访问。

ENG-02正式红：2 failed、31 deselected、2.98秒、exit1；缺租户用SQL delete和新Session确认owner仍active，排除ORM夹具错误。最小修复在cron执行入口重读Tenant并要求存在且active，继承现有后台受控执行规则，不把系统管理API对inactive租户的管理豁免转为后台业务执行权；租户无效只阻断，不误标owner失效或永久关闭任务。ENG-01修复重读计划三字段不一致即拒绝旧快照。源文件已再次冻结，待完整回归及独立重验。
