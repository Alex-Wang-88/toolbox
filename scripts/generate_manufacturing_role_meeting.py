"""Generate a role-based, four-language manufacturing management meeting draft.

This script creates manuscript files only.  It does not synthesize audio.
The meeting follows a fixed boss -> operations -> sales -> finance order so the
task assignments and responses remain easy for downstream semantic analysis.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "test_inputs" / "manufacturing_role_meeting_20m.jsonl"
OUT_MD = ROOT / "test_inputs" / "manufacturing_role_meeting_20m.md"
ROUNDS = 12
TARGET_SEC_PER_TURN = 30

SPEAKERS = {
    "boss": {
        "speaker_id": "speaker_boss",
        "speaker_name": "常凯申",
        "role": "厂长 / 总经理（老板）",
        "voice_id": "changkai_cosyvoice3",
        "language": "普通话",
        "language_code": "zh-CN",
        "instruction": "请用普通话表达。",
        "synthesis_mode": "zero_shot",
        "is_boss": True,
    },
    "operations": {
        "speaker_id": "speaker_operations",
        "speaker_name": "曼波",
        "role": "生产与设备经理",
        "voice_id": "clone_6e5fb002",
        "language": "四川方言",
        "language_code": "zh-SC",
        "instruction": "请用四川话表达。",
        "synthesis_mode": "instruct2",
        "is_boss": False,
    },
    "sales": {
        "speaker_id": "speaker_sales",
        "speaker_name": "特朗普",
        "role": "销售与客户平台主管",
        "voice_id": "clone_099feaae",
        "language": "English",
        "language_code": "en",
        "instruction": "Please speak in English.",
        "synthesis_mode": "cross_lingual",
        "is_boss": False,
    },
    "finance": {
        "speaker_id": "speaker_finance",
        "speaker_name": "魏德尔",
        "role": "财务、采购与合规负责人",
        "voice_id": "clone_weidel",
        "language": "Deutsch",
        "language_code": "de",
        "instruction": "Bitte sprechen Sie Deutsch.",
        "synthesis_mode": "cross_lingual",
        "is_boss": False,
    },
}

ROLE_ORDER = ("boss", "operations", "sales", "finance")


TOPICS = [
    {
        "title": "开场与岗位分工 / Opening and role responsibilities",
        "boss": ""
        "大家上午好，现在召开七月份制造业经营月会。我先把岗位边界定清楚：我负责工厂总目标、重大资源和最终决策；曼波负责生产、设备和现场人员；特朗普负责订单、客户和交付承诺；魏德尔负责财务、采购、供应商合同以及合规。七月份计划产量一万二千八百台，实际入库一万二千四百八十台，按期交付率百分之九十四点六。今天不做重复汇报，每个人只讲自己负责的数字、问题和需要决策的事项。",
        "operations": ""
        "我先从现场说起。A 线完成计划的百分之九十九，B 线只有百分之九十四，C 线完成百分之九十六。B 线是目前最大的瓶颈，晚班有效运行时间少了二十六个小时，三号线还有很多短停。生产这边会把产出、停机、换型和人员安排分开报，不把所有差额都解释成订单问题。",
        "sales": ""
        "From the sales and customer side, the order pool is thirteen thousand two hundred units. Six hundred twenty units are already in next month's delivery window, and thirty-seven orders carry a delay risk. The most sensitive customers are waiting for firm dates on two products that use copper terminals. I will report customer commitments, forecast changes, and complaints separately from the production numbers.",
        "finance": ""
        "Aus Sicht von Finanzen und Einkauf lagen die durchschnittlichen Herstellungskosten bei dreihundertachtzehn Yuan pro Einheit und damit dreizehn Yuan über dem Budget. Die Lagerreichweite stieg auf achtunddreißig Tage, die termingerechte Lieferquote der Lieferanten lag bei zweiundneunzig Komma acht Prozent. Ich trenne heute Preisabweichung, operative Verschwendung, gebundenes Kapital und Compliance-Risiken, damit wir keine Kostenursachen vermischen.",
    },
    {
        "title": "生产、订单与交付联动 / Production, orders, and delivery",
        "boss": ""
        "产量和订单必须放在同一张图上看。生产不能只追求完成数量，销售也不能在没有产能依据时承诺日期。我的要求是先保护高风险客户，再处理低批量换型；任何延期风险都要在承诺日期前暴露。今天要确定一套每周更新的排产、订单和交付联动表，不能月底才发现三个部门用的是三套数字。",
        "operations": ""
        "B 线晚班的问题主要不是人少，而是换型等待和小停太多。平均换型四十二分钟，三号线有四十六次十分钟以内的停机，累起来接近八个小时。我们可以把小批量产品集中到周二和周四换型，把高风险订单锁进白班，但需要销售提前给出优先级，财务确认加班上限。",
        "sales": ""
        "The delivery risk is concentrated rather than spread evenly. We have two copper-terminal products, three German customer orders, and several small batches with tight dates. If operations can give me a reliable capacity window, I can renegotiate sequence without damaging the customer relationship. If the date is not reliable, I would rather communicate a staged shipment than promise a full shipment and miss it.",
        "finance": ""
        "Die vorgeschlagene Umplanung hat direkte Kostenfolgen. Zusätzliche Nachtschichten erhöhen die Lohnkosten, während verspätete Lieferung Vertragsrisiken und Expressfracht auslösen kann. Ich brauche deshalb eine Entscheidung nach Gesamtkosten: Kapazität, Überstunden, Ausschuss, Expressversand und mögliche Vertragsstrafe werden gemeinsam verglichen. Erst danach wird die Lieferzusage freigegeben.",
    },
    {
        "title": "质量与客户投诉 / Quality and customer complaints",
        "boss": ""
        "质量是交付的前提，不是交付之后再补的文件。七月份一次合格率百分之九十八点一，目标是百分之九十八点五；客户投诉十二起，退货四起。我要把现场缺陷和客户声音放在一条闭环里，不能质量部说已经拦截，销售部却还在解释同一种问题。下个月质量指标必须跟客户投诉一起上会。",
        "operations": ""
        "现场主要缺陷是虚焊、外壳划伤和连接器没有装到位。返工率升到百分之一点七，原因不是一个工位独有，换型之后首件确认也不够稳定。我们会把缺陷照片、班组、设备参数和批次绑在一起，先在 B 线提高巡检频率，确认不是靠最后一道检验来兜底。",
        "sales": ""
        "We received twelve complaints, and four became returns. Customers are not only asking for replacement; they want a credible explanation of why the issue happened and why it will not repeat. For German shipments, the label language, lot number, and traceability code must be checked together. I will not close a complaint based only on a refund. The customer needs a corrective action and a verification date.",
        "finance": ""
        "Bei Reklamationen müssen wir Garantie, Rücktransport, Nacharbeit und mögliche Gutschriften getrennt buchen. Die vier Rücksendungen haben zwar keinen großen Gesamtbetrag, aber sie zeigen ein Risiko für die Rückstellung und für die Marge der betroffenen Kunden. Ich möchte außerdem prüfen, ob Lieferantenkosten zurückgefordert werden können. Eine geschlossene Reklamation braucht daher finanzielle und technische Nachweise.",
    },
    {
        "title": "设备效率与投资纪律 / Equipment efficiency and investment discipline",
        "boss": ""
        "设备问题不能靠一次性买新机器解决。当前 OEE 是百分之八十二点四，目标百分之八十五；预防性维护完成率百分之八十八，低于要求。我们先把已有设备的短停和保养做扎实，再讨论资本开支。包装自动化方案的投资回收期超过三十六个月，当前方案停止，不再占用采购预算。",
        "operations": ""
        "三号线的短停主要来自传感器误报警、换型后的参数确认和小故障重复发生。维修团队如果一直被突发故障牵着走，就没有时间做预防性维护。我们需要把关键保养窗口锁进排产表，并为轴承、皮带和传感器设最低库存。设备效率提高以后，才有资格讨论扩产投资。",
        "sales": ""
        "From a customer perspective, equipment reliability matters more than a new machine on a presentation slide. If Line 3 stops repeatedly, the customer sees an unstable promise even when total monthly output looks acceptable. I need a capacity statement that includes planned maintenance, not only theoretical hours. I will use the approved capacity window when I discuss August delivery dates.",
        "finance": ""
        "Die Investitionsdisziplin ist klar: Ein Vorschlag mit mehr als sechsunddreißig Monaten Amortisation wird nicht automatisch genehmigt. Für jede neue Anlage brauchen wir Kapazitätsgewinn, Qualitätswirkung, Wartungskosten und einen realistischen Auslastungsplan. Das gestoppte Verpackungsprojekt bleibt dokumentiert, damit es nicht später ohne neue Bewertung wieder auftaucht.",
    },
    {
        "title": "物料、供应商与库存 / Materials, suppliers, and inventory",
        "boss": ""
        "本月缺料停线四次，库存周转从三十二天升到三十八天，这说明我们同时存在供应不足和库存过多。关键物料要有双供应商，慢动料要有消化计划，不能只让采购催货。八月份我要求把物料齐套率、库存天数和订单风险放进同一张周报，任何红色项目必须有负责人和下一步动作。",
        "operations": ""
        "对生产来说，最麻烦的是铜端子和树脂批次。缺料时产线停下来，来料有问题时又要返工，两个问题会叠在一起。我们会在仓库里把可用料、冻结料和等客户确认的半成品分开，替代料必须先做小批验证，不能临时换上去才发现工艺不适配。",
        "sales": ""
        "The demand signal is changing by customer. Some customers want a staged shipment, while others are asking us to hold semi-finished goods until their specification is confirmed. We should not build every forecast as finished stock. I will separate firm orders, probable orders, and unconfirmed demand, then share the priority list with production and finance every Wednesday.",
        "finance": ""
        "Der Einkauf meldet bei Kupferklemmen einen Preisaufschlag von vier Komma zwei Prozent. Gleichzeitig bindet der Bestand etwa zwei Komma sechs Millionen Yuan. Wir brauchen nicht nur einen niedrigeren Preis, sondern Lieferstabilität, Zahlungsbedingungen und eine belastbare Alternative. Bei jeder Bestellung wird außerdem geprüft, ob sie auf einen bestätigten Auftrag oder nur auf eine unsichere Prognose zurückgeht.",
    },
    {
        "title": "成本、利润与现金 / Cost, margin, and cash",
        "boss": ""
        "单位成本超预算十三元，不能简单要求所有部门平均分摊。材料价格影响六元，返工报废影响三元，加班和外协影响四元，每一项的负责人不同。八月份单位成本目标是三百零八元以内，但前提是不能牺牲安全和质量。请财务把成本变化拆到责任部门，让改善动作和节省金额对应起来。",
        "operations": ""
        "生产能直接控制的是返工、报废、换型损失和加班安排。我们会把每条线的损失按工序拆开，先查重复返工和无计划等待。只要订单优先级提前明确，很多周末加班可以减少；如果临时插单太多，现场就会用加班来掩盖计划问题。",
        "sales": ""
        "Sales can influence margin through order selection and customer negotiation. We have small batches with high handling cost, and some customers request special packaging without covering the extra work. I will flag orders where the promised price does not cover the production and logistics cost. A customer relationship is important, but an order that loses money needs an explicit decision.",
        "finance": ""
        "Die Kostenabweichung wird in Material, Nacharbeit, Ausschuss, Überstunden, Fremdvergabe und Logistik aufgeteilt. Für August erstelle ich eine Brückenrechnung vom Budget zu den tatsächlichen Stückkosten. Dabei wird sichtbar, welcher Betrag durch Einkauf beeinflussbar ist und welcher durch Produktion oder Vertrieb entsteht. Diese Rechnung wird zusammen mit der Liquiditätsplanung und dem Lagerabbau betrachtet.",
    },
    {
        "title": "安全、环保与客户合规 / Safety, environment, and customer compliance",
        "boss": ""
        "安全和合规没有讨价还价的空间。七月份没有损失工时事故，但有两起未遂事件；能源消耗每台一点八二千瓦时，高于一点七五的目标。我们不能把没有受伤理解成没有风险，也不能把客户审核当成临时准备。生产负责现场控制，销售负责客户要求，财务负责证据和预算，月底一起复盘。",
        "operations": ""
        "两起未遂事件分别和叉车倒车视线、维修残余压力有关。下个月我会重新培训叉车路线、挂牌上锁和高温设备操作，班组长每天开工前用三分钟确认风险。能源方面，烘箱空转和压缩空气漏气比较明显，我们会把巡检安排到设备点检表里，不靠口头提醒。",
        "sales": ""
        "Customers are asking for more than product quality. They want evidence of safe production, traceability, energy management, and responsible sourcing. German customers may include these items in an audit questionnaire, so we need consistent answers instead of separate explanations from sales and the plant. I will collect the customer compliance requirements and map each one to an internal record.",
        "finance": ""
        "Für eine Prüfung brauchen wir keine schönen Folien, sondern prüfbare Unterlagen: Schulungslisten, Unfall- und Beinaheereignisse, Energieverbrauch, Abfallnachweise und Lieferantenerklärungen. Wenn Daten nachträglich ergänzt werden, muss die Quelle erkennbar bleiben. Ich stelle eine gemeinsame Belegliste bereit und prüfe, ob für Umwelt- oder Kundenanforderungen zusätzliche Kosten entstehen.",
    },
    {
        "title": "老板派给生产经理的任务 / Boss assigns operations task",
        "task_owner": "曼波",
        "task_owner_role": "生产与设备经理",
        "task_details": "在三条产线真实能力基础上提交十天恢复排产表，包含班次、换型窗口、维护窗口、加班上限、物料替代和每日偏差检查。",
        "deadline": "2026-09-03",
        "status_hint": "未开始",
        "boss": ""
        "现在下达第一个任务给曼波。你负责生产恢复方案，必须根据 A、B、C 三条线的真实能力做出未来十天的排产表，里面要有班次、换型窗口、维护窗口、加班上限、缺料替代和每日偏差检查。截止日期是二零二六年九月三日。这个任务目前还没有启动，但今天会后就要开始，不能等到交付风险变成事实以后再补方案。",
        "operations": ""
        "收到。这个任务的范围我听清楚了，不只是排一个产量数字，还要把换型、维护、物料和加班一起放进去。现在确实还没正式动手，我会先把三条线的实际数据拉出来，明天上午开一个短会，之后按每天的差异调整。九月三号前我交排产表、风险清单和跟踪格式。",
        "sales": ""
        "I will support the operations plan with customer priority. I can provide the list of orders that have a contractual date, the orders that can accept staged shipment, and the orders still waiting for specification confirmation. That information should help Manbo protect the right capacity instead of treating every order as equally urgent.",
        "finance": ""
        "Ich unterstütze diese Aufgabe mit einer Überstunden- und Kostenobergrenze. Die Planung darf keine unkontrollierte Nachtschicht voraussetzen. Sobald der Zehn-Tage-Plan vorliegt, prüfe ich die Mehrkosten, die Materialrisiken und die Liquiditätswirkung. Die Frist am dritten September kommt auch in meinen Prüfkalender.",
    },
    {
        "title": "老板派给销售经理的任务 / Boss assigns sales task",
        "task_owner": "特朗普",
        "task_owner_role": "销售与客户平台主管",
        "task_details": "确认重点客户九月预测、六百二十台积压订单的交付窗口和五个最高风险客户的替代方案，并形成每周三更新的客户承诺表。",
        "deadline": "2026-09-05",
        "status_hint": "进行中",
        "boss": ""
        "第二个任务给特朗普。你负责客户承诺表，具体要确认九月预测、六百二十台积压订单的交付窗口，并为五个最高风险客户准备替代方案。每周三更新一次，不能只报客户说可以或不可以。截止日期是二零二六年九月五日。目前这项工作已经在推进，但最终日期还没有全部锁定，必须把邮件、电话纪要和客户确认一起留档。",
        "operations": ""
        "从生产侧，我会给销售一份真实产能窗口，特别标出 B 线和三号线的维护时间。客户如果接受分批发货，生产可以先保住关键批次；如果客户坚持一次交齐，就要提前看到设备和物料风险。这样销售给出去的日期才有现场依据。",
        "sales": ""
        "Understood. The work is already in progress. I have separated firm orders from forecast demand, and I am contacting the five highest-risk customers. I still need final confirmation on the German delivery windows, so I will not present the table as complete before the replies arrive. By September fifth, I will deliver the customer commitment table, contact evidence, and an alternative plan for each risk account.",
        "finance": ""
        "Ich prüfe zu jedem Kunden auch Zahlungsbedingungen, Gutschriftrisiko und zusätzliche Fracht. Eine Lösung mit Teillieferung kann operativ sinnvoll sein, aber sie darf nicht die Marge oder den Cashflow unbemerkt verschlechtern. Wenn Trump die Kundenantworten sammelt, ergänze ich die wirtschaftliche Bewertung in derselben Tabelle.",
    },
    {
        "title": "老板派给财务采购负责人的任务 / Boss assigns finance and procurement task",
        "task_owner": "魏德尔",
        "task_owner_role": "财务、采购与合规负责人",
        "task_details": "提交关键供应商价格、双供应商覆盖、付款条件、库存现金占用和八元单位成本改善机会的综合方案。",
        "deadline": "2026-09-08",
        "status_hint": "待确认",
        "boss": ""
        "第三个任务给魏德尔。你负责财务、采购和合规的综合方案，要把关键供应商价格、双供应商覆盖、付款条件、库存现金占用和每台节省八元的机会放在同一份文件里。截止日期是二零二六年九月八日。现在还有几家供应商报价和付款条件没有确认，所以这项工作处于等待外部信息的阶段；但你要先把模板和数据口径准备好。",
        "operations": ""
        "我会给魏德尔提供物料消耗、替代料验证和停线影响数据。单看采购单价可能会选到便宜但不稳定的材料，现场需要把缺料停线和来料返工的代价算进去。等价格方案出来以后，我会配合做小批验证，不会直接把新料推到量产线上。",
        "sales": ""
        "I will provide the customer forecast and any special packaging or delivery requirements that affect total cost. A lower supplier price is not useful if it creates a late shipment or a customer complaint. I will mark which demand is confirmed and which demand is still conditional, so finance does not buy material against an uncertain forecast.",
        "finance": ""
        "Verstanden. Ich brauche für den Vergleich noch die ausstehenden Lieferantenangebote und die bestätigten Mengen aus Vertrieb und Produktion. Die Arbeit ist deshalb noch nicht vollständig entscheidungsreif; ich bereite aber bereits das Modell mit Preis, Zahlungsziel, Sicherheitsbestand, Ausschuss und Logistik vor. Bis zum achten September liefere ich die Gesamtlösung mit Quellen und einer Empfehlung für jeden kritischen Lieferanten.",
    },
    {
        "title": "老板自己的任务与风险复盘 / Boss review and risk ownership",
        "task_owner": "常凯申",
        "task_owner_role": "厂长 / 总经理（老板）",
        "task_details": "建立每周经营复盘，主持订单、产能、质量、现金和合规风险的联合检查，并在重大偏差超过阈值时做资源决策。",
        "deadline": "2026-09-01",
        "status_hint": "已完成",
        "boss": ""
        "我也给自己留一个任务：在二零二六年九月一日前建立每周经营复盘，联合检查订单、产能、质量、现金和合规风险，重大偏差超过阈值时由我做资源决策。复盘模板和周一会议已经建立，接下来要坚持使用，不能只在月会上集中救火。这个机制已经搭好，但结果还要靠每周持续执行来证明。",
        "operations": ""
        "有老板主持跨部门复盘，生产这边能更快拿到决策。比如 B 线到底优先维修还是优先加班，现场自己很难同时判断订单和现金影响。我们会在每周会上带着产能、停机和质量数据来，不再只报一句本周完成了多少。",
        "sales": ""
        "A weekly operating review will also help sales. Customer promises change quickly, and I need one approved capacity view before I negotiate dates. I will bring forecast changes, contract risks, and complaint escalation to the meeting. That should prevent customer issues from remaining inside the sales team until they become urgent.",
        "finance": ""
        "Ein wöchentlicher Review hilft auch der Finanzabteilung, weil operative und finanzielle Abweichungen gemeinsam bewertet werden. Ich bringe Cash-Bindung, Preisentwicklung, Forderungen und Compliance-Nachweise mit. Wenn der Schwellenwert überschritten wird, brauchen wir eine Entscheidung mit Datum und Verantwortlichem und nicht nur eine Notiz im Protokoll.",
    },
    {
        "title": "结论与下月承诺 / Closing and next-month commitments",
        "boss": ""
        "今天的结论很明确。曼波负责生产设备，九月三日前交十天恢复排产；特朗普负责客户承诺，九月五日前交重点客户和积压订单方案；魏德尔负责财务采购综合方案，九月八日前交供应商、库存和成本文件；我负责九月一日前启动每周经营复盘。尚未启动的任务要给出启动时间，正在推进的任务要给出证据，外部信息未回齐的任务要保留记录，已经搭好的机制要持续执行。八月份目标是入库一万三千二百台、按期交付率百分之九十七、一次合格率百分之九十八点五，会议到这里结束。",
        "operations": ""
        "我确认生产侧的责任边界和九月三号的交付时间。排产表会把产能、换型、维护、物料和加班放在一起，现场每天看差异。只要销售提前给优先级、财务把成本边界讲清楚，生产就不会靠临时加班来掩盖问题。下个月我会用数据汇报，不只报一个完成数量。",
        "sales": ""
        "I confirm the customer work and the September fifth deadline. I will keep the customer commitment table separate from unconfirmed forecasts, and I will attach contact evidence to every high-risk account. When a customer accepts a staged shipment, I will record the exact window rather than using a vague promise. The sales team will bring real customer feedback into the weekly review.",
        "finance": ""
        "Ich bestätige die Finanz- und Einkaufsaufgabe bis zum achten September. Das Dokument enthält Lieferantenpreise, Zahlungsziele, Sicherheitsbestand, Cash-Bindung und die erwartete Einsparung. Offene Bestätigungen werden klar markiert und nicht als fertige Entscheidung ausgegeben. Wenn alle vier Verantwortungsbereiche jede Woche mit Nachweisen zurückkommen, können wir die Augustziele kontrolliert verfolgen. Vielen Dank, die Sitzung ist beendet.",
    },
]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text, flags=re.UNICODE))


def rough_duration(text: str, role: str) -> float:
    if role in {"boss", "operations"}:
        return round(len(re.sub(r"\s+", "", text)) / 3.5, 1)
    return round(_word_count(text) / (2.15 if role == "sales" else 1.9), 1)


def generate() -> None:
    if len(TOPICS) != ROUNDS:
        raise RuntimeError(f"topics={len(TOPICS)} but rounds={ROUNDS}")
    records = []
    index = 0
    for round_no, topic in enumerate(TOPICS, start=1):
        for turn_no, role_key in enumerate(ROLE_ORDER, start=1):
            index += 1
            speaker = SPEAKERS[role_key]
            text = topic[role_key].strip()
            records.append(
                {
                    "segment_id": f"role_meeting_{index:03d}",
                    "round": round_no,
                    "turn_in_round": turn_no,
                    "topic": topic["title"],
                    "speaker_id": speaker["speaker_id"],
                    "speaker_name": speaker["speaker_name"],
                    "role": speaker["role"],
                    "is_boss": speaker["is_boss"],
                    "voice_id": speaker["voice_id"],
                    "language": speaker["language"],
                    "language_code": speaker["language_code"],
                    "synthesis_mode": speaker["synthesis_mode"],
                    "instruction": speaker["instruction"],
                    "target_duration_sec": TARGET_SEC_PER_TURN,
                    "rough_duration_sec": rough_duration(text, role_key),
                    "task_owner": topic.get("task_owner"),
                    "task_owner_role": topic.get("task_owner_role"),
                    "task_details": topic.get("task_details"),
                    "deadline": topic.get("deadline"),
                    "status_hint": topic.get("status_hint"),
                    "text": text,
                }
            )

    OUT_JSONL.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# 制造业月度管理会：四语岗位化会议稿",
        "",
        "- 目标：至少 20 分钟的角色化制造业月度管理会议",
        f"- 结构：{ROUNDS} 轮 × 4 人 × 每段目标 {TARGET_SEC_PER_TURN} 秒；固定顺序为老板、生产、销售、财务",
        "- 老板：常凯申，厂长 / 总经理，普通话",
        "- 生产与设备经理：曼波，四川方言",
        "- 销售与客户平台主管：特朗普，英语",
        "- 财务、采购与合规负责人：魏德尔，德语",
        "- 任务安排：老板分别给曼波、特朗普、魏德尔以及自己安排任务；正文用自然表达描述进度，JSONL 的 `status_hint` 仅作语义评估参考。",
        "",
    ]
    for item in records:
        lines.extend(
            [
                f"## {item['segment_id']} · 第 {item['round']} 轮 / {item['topic']}",
                f"- 说话人：{item['speaker_name']}（{item['role']}）；音色：`{item['voice_id']}`",
                f"- 语言：{item['language']}；目标时长：{item['target_duration_sec']} 秒；粗略估计：{item['rough_duration_sec']} 秒",
            ]
        )
        if item["task_owner"]:
            lines.extend(
                [
                    f"- 任务负责人：{item['task_owner']}（{item['task_owner_role']}）",
                    f"- 任务细节：{item['task_details']}",
                    f"- 截止日期：{item['deadline']}；语义状态参考：`{item['status_hint']}`",
                ]
            )
        lines.extend(["", item["text"], ""])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    counts = {key: sum(item["speaker_id"] == SPEAKERS[key]["speaker_id"] for item in records) for key in ROLE_ORDER}
    task_topics = sum(1 for topic in TOPICS if topic.get("task_owner"))
    rough_total = round(sum(item["rough_duration_sec"] for item in records) / 60, 1)
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_MD}")
    print(f"segments={len(records)}, per_role={counts}, task_topics={task_topics}, rough_minutes={rough_total}")


if __name__ == "__main__":
    generate()
