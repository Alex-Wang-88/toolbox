"""Generate a four-language manufacturing monthly-review meeting draft.

This script only creates the JSONL/Markdown manuscript.  It does not run TTS.
The spoken text uses natural status phrases, while ``status_hint`` keeps the
semantic ground truth available to downstream evaluation code.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "test_inputs" / "manufacturing_monthly_meeting_1h.jsonl"
OUT_MD = ROOT / "test_inputs" / "manufacturing_monthly_meeting_1h.md"
SEED = 20260818
ROUNDS = 30
TARGET_SEC_PER_TURN = 30

SPEAKERS = {
    "mandarin": {
        "speaker_id": "speaker_mandarin",
        "speaker_name": "常凯申",
        "voice_id": "changkai_cosyvoice3",
        "language": "普通话",
        "language_code": "zh-CN",
        "instruction": "请用普通话表达。",
    },
    "sichuan": {
        "speaker_id": "speaker_sichuan",
        "speaker_name": "曼波",
        "voice_id": "clone_6e5fb002",
        "language": "四川方言",
        "language_code": "zh-SC",
        "instruction": "请用四川话表达。",
    },
    "english": {
        "speaker_id": "speaker_english",
        "speaker_name": "特朗普",
        "voice_id": "clone_099feaae",
        "language": "English",
        "language_code": "en",
        "instruction": "Please speak in English.",
    },
    "german": {
        "speaker_id": "speaker_german",
        "speaker_name": "魏德尔",
        "voice_id": "clone_weidel",
        "language": "Deutsch",
        "language_code": "de",
        "instruction": "Bitte sprechen Sie Deutsch.",
    },
}

SYNTHESIS_MODES = {
    "mandarin": "zero_shot",
    "sichuan": "instruct2",
    "english": "cross_lingual",
    "german": "cross_lingual",
}


# Each topic has four natural-language versions of the same monthly-review
# point.  The figures and deadlines are deliberately repeated so an evaluator
# can align the four languages without requiring literal translation.
TOPICS = [
    {
        "title": "七月经营总览 / July operating overview",
        "mandarin": (
            "大家好，现在开始七月份制造业月度汇报。华东一厂本月计划产量是一万二千八百台，实际入库一万二千四百八十台，完成计划的百分之九十七点五；销售出货一万一千八百六十台，按期交付率百分之九十四点六。平均单位制造成本是三百一十八元，比预算高十三元。今天我们按产量、订单、质量、设备、物料、成本、安全和下月行动项逐项过一遍，所有数字都要能追溯到系统记录。"
        ),
        "sichuan": (
            "各位先把七月份这个制造业月报过一遍。华东一厂原计划要做一万二千八百台，实际入库一万二千四百八十台，完成了百分之九十七点五；出货一万一千八百六十台，按时交付做到百分之九十四点六。平均一台的制造成本三百一十八块，比预算多十三块。今天我们从产量、订单、质量、设备、物料、成本、安全一直摆到下个月的任务，所有数字都要能在系统里头查得到。"
        ),
        "english": (
            "Good morning. This is the July manufacturing monthly review for the East China Plant. The plan called for twelve thousand eight hundred units, while actual receipts reached twelve thousand four hundred eighty, or ninety-seven point five percent of plan. Shipments were eleven thousand eight hundred sixty units, with an on-time delivery rate of ninety-four point six percent. Average manufacturing cost was three hundred eighteen yuan per unit, thirteen yuan above budget. We will review output, orders, quality, equipment, materials, cost, safety, and next-month actions, with every figure traceable to a system record."
        ),
        "german": (
            "Guten Morgen. Dies ist der Monatsbericht für Juli unseres Produktionswerks in Ostchina. Geplant waren zwölftausendachthundert Einheiten, tatsächlich wurden zwölftausendvierhundertachtzig Einheiten eingelagert, also siebenundneunzig Komma fünf Prozent des Plans. Ausgeliefert wurden elftausendachthundertsechzig Einheiten; die termingerechte Lieferquote lag bei vierundneunzig Komma sechs Prozent. Die durchschnittlichen Herstellungskosten betrugen dreihundertachtzehn Yuan pro Einheit und lagen damit dreizehn Yuan über dem Budget. Wir prüfen heute Ausstoß, Aufträge, Qualität, Anlagen, Material, Kosten, Sicherheit und die Maßnahmen für den nächsten Monat; jede Zahl muss im System nachvollziehbar sein."
        ),
    },
    {
        "title": "产量与排产 / Output and production scheduling",
        "mandarin": (
            "产量方面，A 线完成计划的百分之九十九，B 线只有百分之九十四，C 线因为换型和缺料完成百分之九十六。B 线是本月最大的瓶颈，晚班有效开动时间比白班少了二十六小时。我们没有简单地把差额推给加班，而是把每周排产拆到工序，先保证高优先级订单，再安排低批量产品集中换型。八月份要把换型窗口固定在周二和周四，并每天检查计划、实际和剩余产能。"
        ),
        "sichuan": (
            "说到产量，A 线基本完成了，达到计划的百分之九十九；B 线只有百分之九十四，C 线受换型和缺料影响，完成百分之九十六。B 线是这个月最卡的地方，晚班真正开起来的时间比白班少了二十六个小时。我们不光靠加班来补窟窿，要把每周排产拆到每道工序，先保交期紧的单子，再把小批量产品攒到一起换型。八月份固定周二和周四换型，每天把计划、实际产出和剩余能力摆出来看。"
        ),
        "english": (
            "For output, Line A reached ninety-nine percent of plan, Line B reached ninety-four percent, and Line C reached ninety-six percent after changeovers and material shortages. Line B was the main constraint; its effective running time on the night shift was twenty-six hours below the day shift. We should not close the gap by adding overtime without a plan. For August, the schedule will be broken down by process, priority orders will be protected first, and low-volume products will be grouped into fixed changeover windows on Tuesdays and Thursdays. Actual output, plan variance, and remaining capacity must be reviewed every day."
        ),
        "german": (
            "Beim Ausstoß erreichte Linie A neunundneunzig Prozent des Plans, Linie B vierundneunzig Prozent und Linie C sechsundneunzig Prozent. Linie B war der größte Engpass; in der Nachtschicht fehlten im Vergleich zur Tagschicht sechsundzwanzig effektive Betriebsstunden. Wir dürfen die Lücke nicht einfach mit Überstunden kaschieren. Für August wird die Planung auf Prozessebene aufgeteilt, dringende Aufträge werden zuerst abgesichert, und Kleinserien werden in festen Rüstfenstern am Dienstag und Donnerstag gebündelt. Jeden Tag vergleichen wir Plan, tatsächlichen Ausstoß und verbleibende Kapazität."
        ),
    },
    {
        "title": "订单、 backlog 与交付 / Orders, backlog, and delivery",
        "mandarin": (
            "订单池目前有一万三千二百台，其中六百二十台已经进入下月交付窗口。交付团队发现三十七张订单存在延期风险，主要集中在两款使用铜端子的产品。我们要把订单按客户等级、承诺日期和物料齐套率重新排序，不能只看总数量。每周三更新一次交付承诺，如果预计会晚于客户日期，必须在承诺日前至少五个工作日提出替代方案。"
        ),
        "sichuan": (
            "现在手头的订单池有一万三千二百台，其中六百二十台已经挤到下个月的交付窗口里头。交付这边摸出来有三十七张单子可能要迟，主要卡在两款要用铜端子的产品上。我们要按客户重要程度、承诺日期和物料齐不齐重新排，不要只看总数大不大。每周三更新交付承诺，估到可能晚于客户日期，就要提前至少五个工作日拿出替代办法。"
        ),
        "english": (
            "The current order pool contains thirteen thousand two hundred units, including six hundred twenty units already entering next month's delivery window. The delivery team has identified thirty-seven orders at risk of delay, mainly two products that use copper terminals. We need to reprioritize by customer level, committed date, and material readiness instead of looking only at the total volume. Delivery commitments will be refreshed every Wednesday. If a shipment may miss the customer date, an alternative plan must be proposed at least five working days in advance."
        ),
        "german": (
            "Der aktuelle Auftragsbestand umfasst dreizehntausendzweihundert Einheiten; sechshundertzwanzig davon liegen bereits im Lieferfenster des nächsten Monats. Das Lieferteam sieht bei siebenunddreißig Aufträgen ein Verzögerungsrisiko, vor allem bei zwei Produkten mit Kupferklemmen. Wir ordnen die Aufträge nach Kundenpriorität, zugesagtem Termin und Materialvollständigkeit neu und betrachten nicht nur die Gesamtmenge. Jeden Mittwoch aktualisieren wir die Lieferzusagen. Wenn ein Termin gefährdet ist, muss mindestens fünf Arbeitstage vorher eine Alternative vorgelegt werden."
        ),
    },
    {
        "title": "质量指标与缺陷 / Quality indicators and defects",
        "mandarin": (
            "质量部报告本月一次合格率百分之九十八点一，距离百分之九十八点五的目标还差零点四个百分点。主要缺陷是焊点虚焊、外壳划伤和连接器装配不到位，返工率上升到百分之一点七。我们已经把缺陷按工序和班组展开，不能只在最终检验处拦截。下个月要把首件确认、巡检频率和缺陷照片绑定到批次，看到趋势就及时调参数。"
        ),
        "sichuan": (
            "质量这边报的是一次合格率百分之九十八点一，离百分之九十八点五的目标还差零点四个百分点。主要毛病是焊点虚焊、外壳刮花，还有连接器没装到位，返工率涨到了百分之一点七。问题不能只等到最后检验才拦，已经按工序和班组拆开看了。下个月把首件确认、巡检频率和缺陷照片都绑到批次上，哪个趋势往坏头走，就赶紧调参数。"
        ),
        "english": (
            "Quality reported a first-pass yield of ninety-eight point one percent, which is zero point four percentage points below the target of ninety-eight point five. The main defects were cold solder joints, scratched housings, and connectors that were not fully seated. Rework rose to one point seven percent. We have broken the defects down by process and shift instead of relying only on final inspection. Next month, first-piece approval, inspection frequency, and defect photos will be linked to the batch so that parameter changes can be made as soon as a trend appears."
        ),
        "german": (
            "Die Qualität meldete eine Erstpassquote von achtundneunzig Komma eins Prozent und lag damit null Komma vier Prozentpunkte unter dem Ziel von achtundneunzig Komma fünf. Die wichtigsten Fehler waren kalte Lötstellen, Kratzer am Gehäuse und nicht vollständig eingesetzte Steckverbinder. Die Nacharbeitsquote stieg auf ein Komma sieben Prozent. Wir haben die Fehler nach Prozess und Schicht aufgeschlüsselt, statt nur bei der Endprüfung zu reagieren. Im nächsten Monat werden Erstmusterfreigabe, Prüfhäufigkeit und Fehlerbilder mit der Charge verknüpft, damit wir Trends früh erkennen und Parameter anpassen."
        ),
    },
    {
        "title": "设备效率与停机 / Equipment efficiency and downtime",
        "mandarin": (
            "设备综合效率 OEE 本月是百分之八十二点四，低于百分之八十五的目标。损失主要来自三号线短停、换型等待和传感器误报警。维修记录显示，三号线有四十六次十分钟以内的短停，虽然单次不长，但累计影响接近八小时。下月我们要把短停原因从设备、物料、操作和质量四类重新编码，班后必须关闭当天的未解释停机记录。"
        ),
        "sichuan": (
            "设备综合效率 OEE 这个月是百分之八十二点四，没达到百分之八十五的目标。损失主要在三号线小停、换型等起和传感器乱报警。维修记录里头，三号线有四十六次不到十分钟的小停，单次看不长，累起来却接近八个小时。下个月把停机原因重新分成设备、物料、操作和质量四类，每天下班前没解释清楚的停机记录都要关掉，莫要拖到月底才补。"
        ),
        "english": (
            "Overall equipment effectiveness was eighty-two point four percent, below the target of eighty-five. The losses came mainly from short stops on Line 3, waiting during changeovers, and false sensor alarms. Maintenance recorded forty-six stops shorter than ten minutes on Line 3. Each event was small, but together they consumed almost eight hours. Next month, downtime causes will be recoded into equipment, material, operation, and quality categories, and every unexplained stop must be closed before the end of the shift rather than reconstructed at month-end."
        ),
        "german": (
            "Die Gesamtanlageneffektivität lag im Monat bei zweiundachtzig Komma vier Prozent und damit unter dem Ziel von fünfundachtzig Prozent. Die Verluste entstanden vor allem durch Kurzstopps an Linie 3, Wartezeiten beim Rüsten und Fehlalarme der Sensoren. Laut Instandhaltungsprotokoll gab es an Linie 3 sechsundvierzig Stopps von weniger als zehn Minuten; zusammen verursachten sie fast acht Stunden Verlust. Im nächsten Monat werden die Ursachen in Anlage, Material, Bedienung und Qualität eingeteilt. Nicht erklärte Stopps müssen vor Schichtende abgeschlossen werden."
        ),
    },
    {
        "title": "物料与供应商 / Materials and suppliers",
        "mandarin": (
            "供应商按期到货率是百分之九十二点八，本月发生四次缺料停线，其中两次与铜端子有关，一次与树脂批次有关。采购不能只在缺料当天催货，要把供应商承诺、运输节点和替代料审批放在同一张表里。对于影响一万三千二百台订单池的关键物料，至少要保持两家合格供应商，并在周会上说明安全库存还能覆盖多少生产日。"
        ),
        "sichuan": (
            "供应商按时到货做到百分之九十二点八，这个月因为缺料停了四次线，两次卡在铜端子，一次卡在树脂批次。采购莫要等到断料当天才打电话催，要把供应商答应的日期、运输节点和替代料审批放一张表里头。影响一万三千二百台订单的关键料，至少得有两家合格供应商，每周会上还要说清楚安全库存还能撑几天。"
        ),
        "english": (
            "Supplier on-time delivery was ninety-two point eight percent, and material shortages caused four line stoppages this month. Two incidents involved copper terminals and one involved a resin lot. Procurement should not wait until the line is down to call the supplier. Supplier promises, transport milestones, and alternative-material approvals belong on one control sheet. For critical materials supporting the thirteen-thousand-two-hundred-unit order pool, we need at least two qualified suppliers and a weekly statement of how many production days the safety stock can cover."
        ),
        "german": (
            "Die termingerechte Lieferquote der Lieferanten lag bei zweiundneunzig Komma acht Prozent. Wegen Materialmangels kam es in diesem Monat zu vier Linienstopps, davon zwei bei Kupferklemmen und einer bei einer Harzcharge. Der Einkauf darf nicht erst am Tag des Stillstands reagieren. Lieferantenzusage, Transportmeilensteine und Freigabe von Ersatzmaterial gehören in eine gemeinsame Übersicht. Für kritische Materialien des Auftragsbestands von dreizehntausendzweihundert Einheiten brauchen wir mindestens zwei qualifizierte Lieferanten und jede Woche eine Aussage, wie viele Produktionstage der Sicherheitsbestand abdeckt."
        ),
    },
    {
        "title": "库存与现金占用 / Inventory and cash usage",
        "mandarin": (
            "库存周转天数从三十二天升到三十八天，主要是两种慢动原料和一批等待客户确认的半成品。财务估算这部分库存占用了约二百六十万元现金。生产、采购和销售要一起看库存，而不是各自只看自己的表。八月份先消化可替代订单的半成品，再冻结没有明确需求的补货，月底把超过六十天没有动用的物料列出处理建议。"
        ),
        "sichuan": (
            "库存周转从三十二天拖到了三十八天，主要是两种慢料，还有一批等客户确认的半成品。财务算下来，这一坨库存压了大概二百六十万现金。生产、采购和销售要一起看库存，莫要各看各的表。八月份先把能挪到别的订单上的半成品消化掉，没有明确需求的补货先按住，月底把超过六十天没动过的料列出来拿处理办法。"
        ),
        "english": (
            "Inventory days increased from thirty-two to thirty-eight, mainly because of two slow-moving materials and a batch of semi-finished goods waiting for customer confirmation. Finance estimates that this stock ties up about two point six million yuan in cash. Production, procurement, and sales must review inventory together rather than optimizing separate spreadsheets. In August, we will consume semi-finished goods that can be reassigned to other orders, pause replenishment without clear demand, and present a disposition proposal for materials that have not moved for more than sixty days."
        ),
        "german": (
            "Die Lagerreichweite stieg von zweiunddreißig auf achtunddreißig Tage. Ursache sind vor allem zwei langsam drehende Materialien und eine Charge Halbfertigprodukte, die auf eine Kundenbestätigung wartet. Nach Schätzung des Finanzbereichs bindet dieser Bestand etwa zwei Komma sechs Millionen Yuan. Produktion, Einkauf und Vertrieb müssen den Bestand gemeinsam betrachten. Im August verbrauchen wir Halbfertigprodukte, die anderen Aufträgen zugeordnet werden können, stoppen Nachbeschaffung ohne klare Nachfrage und legen für Materialien ohne Bewegung von mehr als sechzig Tagen einen Behandlungsvorschlag vor."
        ),
    },
    {
        "title": "预防性维护 / Preventive maintenance",
        "mandarin": (
            "预防性维护完成率本月是百分之八十八，低于百分之九十五的要求。未完成的项目大多是周末窗口太短，维修人员优先处理故障，计划保养被往后挪。我们要把关键设备的保养时间锁进排产表，备件库对轴承、皮带和传感器设置最低库存。维护不能只报完成率，还要报告保养后故障间隔有没有延长。"
        ),
        "sichuan": (
            "预防性维护这个月做到了百分之八十八，离百分之九十五的要求还有差距。没做完的多半是周末窗口太短，维修师傅先去处理坏掉的设备，计划保养就往后挪。关键设备的保养时间要锁到排产表里头，备件库给轴承、皮带和传感器设最低库存。维护不能只报做了好多，还要看保养以后故障间隔有没有拉长。"
        ),
        "english": (
            "Preventive maintenance completion was eighty-eight percent, below the required ninety-five percent. Most missed tasks were pushed out because weekend windows were too short and technicians had to handle breakdowns first. Maintenance time for critical equipment must be locked into the production schedule. The spare-parts room needs minimum levels for bearings, belts, and sensors. The report should not stop at completion rate; it should also show whether the interval between failures improved after maintenance."
        ),
        "german": (
            "Die Erfüllung der vorbeugenden Instandhaltung lag bei achtundachtzig Prozent und damit unter den geforderten fünfundneunzig Prozent. Viele Aufgaben wurden verschoben, weil die Wochenendfenster zu kurz waren und die Techniker zuerst Störungen beheben mussten. Die Wartungszeit kritischer Anlagen wird künftig fest in den Produktionsplan aufgenommen. Für Lager, Riemen und Sensoren braucht das Ersatzteillager Mindestbestände. Der Bericht soll nicht nur die Erfüllungsquote zeigen, sondern auch, ob sich das Intervall zwischen Ausfällen nach der Wartung verlängert hat."
        ),
    },
    {
        "title": "能源与环保 / Energy and environmental performance",
        "mandarin": (
            "本月单位产品用电量是一点八二千瓦时，高于一点七五的目标，峰值电价时段的耗电占比也上升了。能源小组发现烘箱空载运行和压缩空气泄漏是两个明显问题。八月份要把烘箱开机和批次计划联动，安排一次全厂气路巡检，同时每天记录峰谷电量。环保方面没有超标排放，但废包装分类准确率只有百分之九十一，还需要改善。"
        ),
        "sichuan": (
            "这个月每台产品用电一点八二度，比一点七五的目标高，峰值电价时段用的电也多了。能源小组查到烘箱空转和压缩空气漏气是两个大问题。八月份把烘箱开机跟批次排产连起来，全厂气路巡一遍，每天还要记峰谷电量。环保排放没有超标，不过废包装分得不够细，准确率只有百分之九十一，这个也要补上。"
        ),
        "english": (
            "Electricity consumption was one point eighty-two kilowatt-hours per unit, above the target of one point seventy-five, and the share consumed during peak-price periods also increased. The energy team found idle oven operation and compressed-air leaks as two visible causes. In August, oven start-up will be linked to the batch schedule, the entire air network will be inspected, and peak and off-peak consumption will be recorded daily. Emissions stayed within limits, but waste-packaging sorting accuracy was only ninety-one percent and needs improvement."
        ),
        "german": (
            "Der Stromverbrauch lag bei eins Komma zweiundachtzig Kilowattstunden pro Einheit und damit über dem Ziel von eins Komma fünfundsiebzig. Außerdem stieg der Anteil des Verbrauchs in den teuren Spitzenzeiten. Das Energieteam fand zwei deutliche Ursachen: Leerlaufbetrieb der Öfen und Leckagen in der Druckluft. Im August wird der Ofenstart mit der Chargenplanung verbunden, das gesamte Druckluftnetz geprüft und der Verbrauch in Hoch- und Nebenzeiten täglich erfasst. Die Emissionen blieben innerhalb der Grenzwerte, aber die Sortiergenauigkeit bei Verpackungsabfällen lag nur bei einundneunzig Prozent."
        ),
    },
    {
        "title": "安全与应急 / Safety and emergency readiness",
        "mandarin": (
            "安全方面本月没有损失工时事故，但记录了两起未遂事件，一起是叉车倒车时视线被货架挡住，另一起是维修时没有先确认残余压力。我们不能因为没有受伤就把它们当作小事。每起事件都要有现场照片、原因分析和防止再发生的动作。下个月重点复训叉车路线、挂牌上锁和高温设备作业，班组长每天开工前要用三分钟确认风险。"
        ),
        "sichuan": (
            "安全这块儿这个月没有伤工事故，不过有两起差点出事的情况：一次是叉车倒车被货架挡了视线，另一次维修的时候没先确认还有没有压力。没受伤不等于没问题，这两起都要留现场照片、找原因、定防再发措施。下个月重点把叉车路线、挂牌上锁和高温设备操作再培训一遍，班组长每天开工前花三分钟把风险说清楚。"
        ),
        "english": (
            "There were no lost-time injuries this month, but we recorded two near misses. In one case, a forklift operator had a blocked view while reversing; in the other, maintenance began before residual pressure was confirmed. The absence of injury does not make either case minor. Each event needs photos, a cause analysis, and a prevention action. Next month we will retrain forklift routes, lockout and tagout, and hot-equipment work. Supervisors will spend three minutes confirming risks before each shift starts."
        ),
        "german": (
            "In diesem Monat gab es keinen Arbeitsunfall mit Ausfallzeit, aber zwei Beinaheereignisse. Ein Gabelstaplerfahrer hatte beim Rückwärtsfahren eine durch ein Regal eingeschränkte Sicht; bei einem anderen Fall begann die Wartung, bevor der Restdruck bestätigt war. Dass niemand verletzt wurde, macht die Fälle nicht unbedeutend. Für jedes Ereignis brauchen wir Fotos, Ursachenanalyse und eine Vorbeugemaßnahme. Im nächsten Monat schulen wir Fahrwege, Lockout-Tagout und Arbeiten an heißen Anlagen. Vor jeder Schicht bestätigt der Meister drei Minuten lang die Risiken."
        ),
    },
    {
        "title": "人员、加班与技能 / Workforce, overtime, and skills",
        "mandarin": (
            "人员方面，平均出勤率百分之九十七点二，新员工有十八人，培训工时还没有完全计入班组计划。B 线连续三周加班，部分员工每周超过十二小时，需要关注疲劳风险。人事和生产要把技能矩阵更新到工序级别，不能只写会操作某条线。八月份每个班组至少安排两次交叉培训，并把能独立处理换型和首检的人员标出来。"
        ),
        "sichuan": (
            "人员这边平均出勤百分之九十七点二，新来了十八个人，但培训工时还没完全放进班组计划。B 线连续加了三周班，有些员工一周加班超过十二个小时，疲劳风险要盯住。人事和生产把技能矩阵细到工序上，莫只写一句会操作这条线。八月份每个班组至少搞两次交叉培训，还要标出哪些人能独立换型、做首检。"
        ),
        "english": (
            "Average attendance was ninety-seven point two percent, and eighteen new employees joined the plant. Training hours for the new hires are not yet fully reflected in the team plans. Line B has worked overtime for three consecutive weeks, with some employees exceeding twelve overtime hours per week, so fatigue risk needs attention. Human resources and production will update the skills matrix down to the process level. Each team will hold at least two cross-training sessions in August and identify people who can independently handle changeovers and first-piece checks."
        ),
        "german": (
            "Die durchschnittliche Anwesenheit lag bei siebenundneunzig Komma zwei Prozent, und achtzehn neue Beschäftigte kamen hinzu. Die Schulungsstunden der neuen Mitarbeiter sind noch nicht vollständig in den Teamplänen berücksichtigt. Linie B leistete drei Wochen in Folge Überstunden; einige Beschäftigte überschritten zwölf Überstunden pro Woche, daher müssen wir das Ermüdungsrisiko beachten. Personal und Produktion aktualisieren die Qualifikationsmatrix bis auf Prozessebene. Jedes Team führt im August mindestens zwei Schulungen zum Arbeitsplatzwechsel durch und benennt Personen, die Rüstvorgänge und Erstmusterprüfungen selbstständig durchführen können."
        ),
    },
    {
        "title": "成本与预算偏差 / Cost and budget variance",
        "mandarin": (
            "财务把单位成本超预算的十三元拆开了：材料价格影响六元，返工和报废影响三元，加班和外协影响四元。材料价格是外部因素，但返工和加班属于内部可以管理的部分。我们不能只要求生产把成本降下来，还要把每个改善动作和预计节省金额写进月度表。八月份先盯住铜端子替代、减少换型损失和控制周末加班三个方向。"
        ),
        "sichuan": (
            "财务把每台多出来的十三块成本拆开看了：材料涨价占六块，返工报废占三块，加班和外协占四块。材料价格有外部原因，但返工和加班是我们自己能管的地方。不能光喊生产把成本降下来，每个改善动作和预计能省好多钱都要写到月报里头。八月份先盯铜端子替代、少损失点换型时间，还有把周末加班控制住。"
        ),
        "english": (
            "Finance decomposed the thirteen-yuan unit-cost overrun. Material price changes contributed six yuan, rework and scrap contributed three, and overtime plus outsourcing contributed four. Material prices are external, but rework and overtime are manageable internally. We should not simply ask production to reduce cost; each improvement action must include an estimated saving in the monthly tracker. For August, focus on copper-terminal substitution, reducing changeover loss, and controlling weekend overtime."
        ),
        "german": (
            "Die Finanzabteilung hat die Überschreitung von dreizehn Yuan pro Einheit aufgeschlüsselt. Sechs Yuan entfielen auf Materialpreise, drei auf Nacharbeit und Ausschuss, vier auf Überstunden und Fremdvergabe. Materialpreise sind ein externer Faktor, Nacharbeit und Überstunden können wir jedoch intern steuern. Wir dürfen nicht nur eine Kostensenkung fordern; jede Maßnahme muss im Monatsplan mit einer erwarteten Einsparung stehen. Im August konzentrieren wir uns auf den Ersatz der Kupferklemmen, weniger Rüstverluste und die Begrenzung von Wochenendüberstunden."
        ),
    },
    {
        "title": "客户投诉与退货 / Customer complaints and returns",
        "mandarin": (
            "客户本月提出十二起投诉，四起已经形成退货，集中在连接器松动、外观划伤和包装标签不清。质量团队完成了其中七起的原因调查，但还有五起只停留在临时纠正。八月份必须把客户投诉分成设计、工艺、供应商和物流四类，每一类都要有永久措施和验证证据。对于德国客户的批次，标签要同时核对语言、批号和追溯二维码。"
        ),
        "sichuan": (
            "客户这个月提了十二起投诉，已经退回来四起，主要是连接器松、外壳有划痕、包装标签看不清。质量组查完了七起，还有五起只是先把眼前的问题压住，真正的根因还没关。八月份把投诉分成设计、工艺、供应商和物流四类，每类都要有长期措施和验证证据。德国客户那几批货，语言、批号和追溯二维码要一起对，莫要只看其中一项。"
        ),
        "english": (
            "Customers raised twelve complaints this month, and four became returns. The main issues were loose connectors, scratched housings, and unclear package labels. Quality completed investigations for seven cases, while five still have only temporary corrections. In August, complaints must be classified as design, process, supplier, or logistics issues, with a permanent action and verification evidence for each class. For shipments to German customers, the language, lot number, and traceability QR code on every label must be checked together."
        ),
        "german": (
            "Kunden meldeten in diesem Monat zwölf Beanstandungen; vier davon führten zu Rücksendungen. Die Hauptprobleme waren lose Steckverbinder, Kratzer am Gehäuse und unklare Verpackungsetiketten. Die Qualität hat sieben Fälle untersucht, bei fünf Fällen gibt es bisher nur eine vorläufige Korrektur. Im August ordnen wir Beanstandungen den Kategorien Design, Prozess, Lieferant oder Logistik zu; jede Kategorie braucht eine dauerhafte Maßnahme und einen Wirksamkeitsnachweis. Für Lieferungen an deutsche Kunden werden Sprache, Chargennummer und Rückverfolgungs-QR-Code gemeinsam geprüft."
        ),
    },
    {
        "title": "精益改善与换型 / Lean improvement and changeovers",
        "mandarin": (
            "精益小组把一条产品族的平均换型时间从四十二分钟降到了三十一分钟，主要靠提前准备工装和把首件物料放到线边。这个改善已经在 A 线验证，但 B 线还没有完全照做。下一步不是马上宣布成功，而是连续观察十个批次，确认安全、质量和交付没有副作用。改善报告要留下前后数据、操作标准和培训照片，方便复制到其他产线。"
        ),
        "sichuan": (
            "精益小组把一类产品的平均换型从四十二分钟降到三十一分钟，靠的是提前准备工装，还有把首件物料放到线边。A 线已经试过了，B 线还没完全跟上。下一步莫要马上说成功，要连续盯十个批次，看看安全、质量和交付有没有啥子副作用。改善报告把前后数据、操作标准和培训照片留齐，后面才好复制到别的产线。"
        ),
        "english": (
            "The lean team reduced average changeover time for one product family from forty-two to thirty-one minutes by preparing tooling in advance and placing first-piece material at the line. The improvement has been verified on Line A, but Line B has not fully adopted it. We should not declare success yet. We need to observe ten consecutive batches and confirm that safety, quality, and delivery are not harmed. The improvement report must retain before-and-after data, the standard work, and training photos so it can be copied to other lines."
        ),
        "german": (
            "Das Lean-Team hat die durchschnittliche Rüstzeit einer Produktfamilie von zweiundvierzig auf einunddreißig Minuten reduziert. Dafür wurden Werkzeuge vorher vorbereitet und das Material für das Erstmuster an die Linie gelegt. Die Verbesserung ist an Linie A bestätigt, an Linie B aber noch nicht vollständig umgesetzt. Wir erklären sie noch nicht für endgültig erfolgreich, sondern beobachten zehn aufeinanderfolgende Chargen und prüfen Sicherheit, Qualität und Lieferfähigkeit. Der Bericht enthält Vorher-Nachher-Daten, Standardarbeit und Schulungsfotos, damit die Methode auf andere Linien übertragen werden kann."
        ),
    },
    {
        "title": "数字化与追溯 / Digitalization and traceability",
        "mandarin": (
            "MES 数据完整率本月达到百分之九十六点八，但有三类记录容易缺失：换型开始结束时间、返工原因和人工补录的检验结果。数字化团队要把这三类字段设成必填，并让条码扫描和工单自动关联。数据不是为了填表，而是为了在客户投诉时能回答哪批料、哪台设备、哪位班组和哪个参数造成了结果。八月份先在 A 线做两周试运行，再决定是否全厂推广。"
        ),
        "sichuan": (
            "MES 数据完整率这个月到了百分之九十六点八，不过有三类东西容易漏：换型啥时候开始结束、返工为啥子返，还有人工补录的检验结果。数字化这边要把这三类设成必填，条码扫描也要跟工单自动绑起来。数据不是为了把表填满，而是客户问起来时，能查到哪批料、哪台设备、哪个班组和哪个参数带来的结果。八月份先在 A 线试两周，效果看清楚了再决定要不要全厂推。"
        ),
        "english": (
            "MES data completeness reached ninety-six point eight percent, but three records are often missing: changeover start and finish times, rework reasons, and inspection results entered manually. The digital team will make these fields mandatory and connect barcode scans automatically to the work order. Data is not collected just to fill a form. When a customer asks a question, we need to identify the material lot, machine, shift team, and parameter behind the result. We will run a two-week pilot on Line A in August before deciding on plant-wide rollout."
        ),
        "german": (
            "Die Vollständigkeit der MES-Daten erreichte sechsundneunzig Komma acht Prozent. Häufig fehlen jedoch drei Angaben: Beginn und Ende des Rüstens, der Grund für Nacharbeit und manuell eingetragene Prüfergebnisse. Das Digitalteam macht diese Felder verpflichtend und verknüpft Barcodes automatisch mit dem Auftrag. Daten dienen nicht nur dem Ausfüllen von Formularen. Bei einer Kundenfrage müssen wir Materialcharge, Anlage, Schichtteam und den maßgeblichen Parameter finden können. Im August testen wir das zwei Wochen an Linie A und entscheiden danach über die Ausweitung auf das gesamte Werk."
        ),
    },
    {
        "title": "任务一：恢复排产 / Task one: recover the production plan",
        "task_owner": "常凯申",
        "task_owner_voice_id": "changkai_cosyvoice3",
        "task_details": "根据三条产线的真实能力重排八月每周计划，优先保障高风险订单，列出换型窗口、加班上限和缺料替代方案，并每天更新计划与实际差异。",
        "deadline": "2026-09-05",
        "status_hint": "未开始",
        "mandarin": (
            "下面明确第一个行动项。常凯申负责恢复八月份排产，具体要根据 A、B、C 三条线的真实产能重排每周计划，优先保护高风险订单，列出换型窗口、加班上限和缺料时的替代方案，并且每天更新计划与实际差异。这项工作目前还没有启动，先不要把它写成已经完成。负责人要在二零二六年九月五日前提交周计划、风险清单和每日跟踪表，生产经理在提交后一个工作日内复核。"
        ),
        "sichuan": (
            "第一个行动项说清楚哈。常凯申来负责恢复八月份排产，要按 A、B、C 三条线真实能做好多来重排每周计划，先保风险高的订单，换型窗口、加班上限和缺料时咋个替代都要写出来，每天还要更新计划跟实际差多少。这项事情目前还没启动，莫要先在表上打勾说做完了。负责人要在二零二六年九月五号前交周计划、风险清单和每日跟踪表，生产经理收到以后一个工作日内复核。"
        ),
        "english": (
            "Let us make the first action item explicit. Chang Kaishen owns the August production recovery plan. The work includes rebuilding the weekly schedule from the real capacity of Lines A, B, and C, protecting high-risk orders, listing changeover windows and an overtime limit, and defining substitutes for material shortages. The work has not started yet, so it must not be described as finished. The owner must submit the weekly plan, risk list, and daily variance tracker by September fifth, twenty twenty-six. The production manager will review them within one working day."
        ),
        "german": (
            "Wir legen den ersten Aktionspunkt eindeutig fest. Chang Kaishen übernimmt die Wiederherstellung der August-Produktionsplanung. Dazu gehören eine wöchentliche Planung auf Basis der tatsächlichen Kapazität von Linie A, B und C, der Schutz risikoreicher Aufträge, feste Rüstfenster, eine Obergrenze für Überstunden und Alternativen bei Materialmangel. Diese Arbeit hat noch nicht begonnen und darf daher nicht als erledigt dargestellt werden. Der Verantwortliche legt bis zum fünften September zweitausendsechsundzwanzig den Wochenplan, die Risikoliste und den täglichen Abweichungstracker vor. Der Produktionsleiter prüft die Unterlagen innerhalb eines Arbeitstags."
        ),
    },
    {
        "title": "任务二：供应商与来料质量 / Task two: supplier and incoming quality",
        "task_owner": "曼波",
        "task_owner_voice_id": "clone_6e5fb002",
        "task_details": "完成铜端子和树脂批次的来料抽检规则，推动两家供应商提交纠正措施，关闭三起缺料或来料缺陷，并输出含证据的周报。",
        "deadline": "2026-09-07",
        "status_hint": "进行中",
        "mandarin": (
            "第二个行动项交给曼波。曼波负责供应商和来料质量，任务细节包括重新确认铜端子与树脂批次的抽检规则，推动两家供应商提交纠正措施，关闭三起缺料或来料缺陷，并每周输出带照片、批号和验证结果的报告。这项工作已经在推进，第一轮数据正在收集，不能只说已经开会。截止日期是二零二六年九月七日，采购经理和质量经理要在报告上共同签字。"
        ),
        "sichuan": (
            "第二个行动项交给曼波来牵头。曼波负责供应商和来料质量，要把铜端子、树脂批次的抽检规则重新定清楚，推动两家供应商交纠正措施，关掉三起缺料或者来料缺陷，还要每周出一份带照片、批号和验证结果的报告。这个事已经在往前推进，第一轮数据正在收集，莫要只说开过会就算数。截止到二零二六年九月七号，采购经理和质量经理要一起在报告上签字。"
        ),
        "english": (
            "The second action item goes to Manbo. Manbo owns supplier and incoming quality. The details are to reconfirm sampling rules for copper terminals and resin lots, obtain corrective actions from two suppliers, close three shortage or incoming-defect cases, and issue a weekly report with photos, lot numbers, and verification results. The work is already moving forward and the first data set is being collected; holding a meeting alone is not enough. The deadline is September seventh, twenty twenty-six, and both the procurement manager and the quality manager must sign the report."
        ),
        "german": (
            "Der zweite Aktionspunkt geht an Manbo. Manbo verantwortet Lieferanten- und Wareneingangsqualität. Die Aufgabe umfasst neue Prüfregeln für Kupferklemmen und Harzchargen, Korrekturmaßnahmen von zwei Lieferanten, den Abschluss von drei Fällen zu Materialmangel oder Eingangsfehlern sowie einen Wochenbericht mit Fotos, Chargennummern und Wirksamkeitsnachweisen. Die Arbeit läuft bereits, die ersten Daten werden gesammelt; eine Besprechung allein reicht nicht. Die Frist ist der siebte September zweitausendsechsundzwanzig. Einkaufs- und Qualitätsleiter müssen den Bericht gemeinsam unterschreiben."
        ),
    },
    {
        "title": "任务三：设备效率与能源看板 / Task three: equipment and energy dashboard",
        "task_owner": "特朗普",
        "task_owner_voice_id": "clone_099feaae",
        "task_details": "完成三号线短停分类、传感器误报警分析、预防性维护窗口和峰谷用电看板，给出 OEE 提升到 85% 的验证结果。",
        "deadline": "2026-09-10",
        "status_hint": "已完成",
        "mandarin": (
            "第三个行动项由特朗普负责设备效率和能源看板。细节包括完成三号线短停分类，分析传感器误报警，确认预防性维护窗口，建立峰谷用电看板，并用数据验证 OEE 能否提升到百分之八十五。本月的基线、停机编码和第一版看板已经完成，接下来要把结果交给生产和财务复核。请在二零二六年九月十日前提交最终看板、故障分类表和提升验证报告，不能只交截图。"
        ),
        "sichuan": (
            "第三个行动项让特朗普来管设备效率和能源看板。要把三号线小停分好类，查传感器为啥子乱报警，把预防性维护窗口定下来，峰谷用电看板也要立起，还要用数据验证 OEE 能不能拉到百分之八十五。本月基线、停机编码和第一版看板已经弄完了，后头交给生产和财务复核。二零二六年九月十号前交最终看板、故障分类表和提升验证报告，莫只甩一张截图过来。"
        ),
        "english": (
            "The third action item is assigned to Donald Trump for equipment efficiency and the energy dashboard. The details are to classify short stops on Line 3, analyze false sensor alarms, confirm preventive-maintenance windows, build a peak and off-peak energy dashboard, and verify with data whether OEE can reach eighty-five percent. The baseline, downtime codes, and first dashboard have already been completed this month. The remaining step is a review by production and finance. Submit the final dashboard, fault classification table, and improvement verification report by September tenth, twenty twenty-six; a screenshot alone is not acceptable."
        ),
        "german": (
            "Der dritte Aktionspunkt wird Donald Trump für Anlageneffizienz und Energiedashboard zugewiesen. Zu den Details gehören die Klassifizierung der Kurzstopps an Linie 3, die Analyse von Fehlalarmen, die Festlegung von Wartungsfenstern, ein Dashboard für Spitzen- und Nebenzeiten sowie der Nachweis, ob die OEE auf fünfundachtzig Prozent steigen kann. Basisdaten, Stillstandscodes und die erste Dashboard-Version sind in diesem Monat bereits fertiggestellt. Nun prüfen Produktion und Finanzen die Ergebnisse. Bis zum zehnten September zweitausendsechsundzwanzig sind Dashboard, Fehlerklassifizierung und Validierungsbericht einzureichen; ein Screenshot genügt nicht."
        ),
    },
    {
        "title": "任务四：德国客户交付确认 / Task four: German customer delivery confirmation",
        "task_owner": "魏德尔",
        "task_owner_voice_id": "clone_weidel",
        "task_details": "与德国客户确认九月预测和六百二十台积压订单的交付窗口，形成分批发运计划，核对德语标签、批号和二维码。",
        "deadline": "2026-09-12",
        "status_hint": "待确认",
        "mandarin": (
            "第四个行动项由魏德尔负责德国客户交付确认。具体要和客户确认九月份预测以及六百二十台积压订单的交付窗口，形成分批发运计划，同时核对德语标签、批号和追溯二维码。现在还在等客户确认最终日期，所以不能把交付计划当成定稿。负责人要在二零二六年九月十二日前提交客户回信、分批发运表和标签检查记录；如果客户没有回复，要留下跟进证据并升级处理。"
        ),
        "sichuan": (
            "第四个行动项请魏德尔来负责德国客户的交付确认。要跟客户确认九月份预测，还有六百二十台积压订单到底哪个窗口发，做成分批发运计划，德语标签、批号和追溯二维码也要一块儿核。现在还在等客户把最后日期确认下来，所以这个计划还不能说已经定死。二零二六年九月十二号前交客户回信、分批发运表和标签检查记录；客户没回的话，跟进证据要留好，还要往上升级。"
        ),
        "english": (
            "The fourth action item assigns German-customer delivery confirmation to Weidel. The work is to confirm the September forecast and the delivery windows for the six hundred twenty-unit backlog, create a staged-shipment plan, and check German labels, lot numbers, and traceability QR codes. We are still waiting for the customer to confirm the final dates, so the shipment plan is not final. The owner must submit the customer reply, staged-shipment table, and label-check record by September twelfth, twenty twenty-six. If the customer does not reply, the follow-up evidence must be retained and escalated."
        ),
        "german": (
            "Der vierte Aktionspunkt überträgt Weidel die Bestätigung der Lieferungen an den deutschen Kunden. Die Aufgabe besteht darin, die Septemberprognose und Lieferfenster für den Rückstand von sechshundertzwanzig Einheiten zu bestätigen, einen gestaffelten Versandplan zu erstellen und deutsche Etiketten, Chargennummern sowie Rückverfolgungs-QR-Codes zu prüfen. Wir warten noch auf die endgültige Kundenbestätigung; der Versandplan ist daher noch nicht final. Bis zum zwölften September zweitausendsechsundzwanzig müssen Kundenantwort, Versandtabelle und Etikettenprüfprotokoll vorliegen. Bei ausbleibender Antwort werden Nachweise gespeichert und eskaliert."
        ),
    },
    {
        "title": "任务五：包装自动化试点 / Task five: packaging automation pilot",
        "task_owner": "曼波",
        "task_owner_voice_id": "clone_6e5fb002",
        "task_details": "评估自动贴标和装箱试点的投资回收期、质量风险和产能收益；由于回收期超过三十六个月，停止当前方案并保留评估记录。",
        "deadline": "2026-09-15",
        "status_hint": "已取消",
        "mandarin": (
            "最后一个专项任务是包装自动化试点。曼波已经完成了自动贴标和装箱方案的投入产出评估，结果显示投资回收期超过三十六个月，而且会增加换线复杂度。基于这个结果，当前试点方案已经停止，不再继续采购设备，但评估表、供应商报价和停止原因必须保存。九月十五日前把结论归档，并把预算释放情况告知财务；以后如果有更小范围的半自动方案，可以重新提出，但不能沿用这次已停止的方案。"
        ),
        "sichuan": (
            "最后一个专项任务说包装自动化。曼波已经把自动贴标和装箱方案的投入产出算过了，发现回收期超过三十六个月，还会把换线搞复杂。根据这个结果，当前试点已经停了，不再继续买设备，不过评估表、供应商报价和停止原因都要留档。九月十五号前把结论归档，再把释放出来的预算告诉财务。以后要是有更小的半自动办法，可以重新提，不能把这次停掉的方案又偷偷接起来。"
        ),
        "english": (
            "The final special item is the packaging-automation pilot. Manbo has completed the cost-benefit review of automatic labeling and carton packing. The payback period exceeds thirty-six months and the proposal would add changeover complexity. Based on that result, the current pilot has been stopped and equipment purchasing will not continue, but the evaluation sheet, supplier quotations, and reason for stopping must be retained. Archive the conclusion by September fifteenth and inform finance how the budget is released. A smaller semi-automatic idea can be proposed later, but this stopped proposal must not quietly restart."
        ),
        "german": (
            "Der letzte Sonderpunkt ist der Pilot für die Verpackungsautomatisierung. Manbo hat die Wirtschaftlichkeit von automatischer Etikettierung und Kartonverpackung geprüft. Die Amortisationszeit liegt über sechsunddreißig Monaten, außerdem würde der Vorschlag die Rüstkomplexität erhöhen. Deshalb wurde der aktuelle Pilot beendet und der Geräteeinkauf wird nicht fortgesetzt. Bewertungsbogen, Lieferantenangebote und der Grund für die Beendigung bleiben jedoch erhalten. Bis zum fünfzehnten September wird das Ergebnis archiviert und der Finanzbereich über die Budgetfreigabe informiert. Eine kleinere halbautomatische Lösung kann später neu vorgeschlagen werden."
        ),
    },
    {
        "title": "风险排序与决策 / Risk ranking and decisions",
        "mandarin": (
            "把风险放在一起看，最高的是 B 线能力不足、铜端子供应不稳和德国客户日期未确认，这三项会直接影响交付。第二层是 OEE 低于目标、单位成本偏高和库存周转变慢，影响利润但还有缓冲。安全未遂事件和标签错误属于必须立即控制的风险，不能因为数量少就降低优先级。今天的决定是先保订单和人身安全，再用八月份的任务表逐项关闭风险，所有变更必须留下日期和负责人。"
        ),
        "sichuan": (
            "把风险摆一堆看，最急的是 B 线能力不够、铜端子供应不稳，还有德国客户的日期没定，这三个会直接扯到交付。第二层是 OEE 没到目标、单台成本偏高、库存转得慢，主要影响利润，还留得有缓冲。安全差点出事和标签搞错属于马上要控的风险，数量少也不能往后排。今天就定了，先保订单和人身安全，再照八月份任务表一项一项关风险，改了啥子都要留下日期和负责人。"
        ),
        "english": (
            "When the risks are viewed together, the top three are insufficient capacity on Line B, unstable copper-terminal supply, and unconfirmed dates from the German customer. Each can directly affect delivery. The second tier is OEE below target, high unit cost, and slower inventory turnover; these affect profit but still have some buffer. Near misses and label errors require immediate control even if the count is small. Today's decision is to protect orders and people first, then close the risks through the August action tracker, with a date and owner recorded for every change."
        ),
        "german": (
            "In der Gesamtsicht stehen die unzureichende Kapazität von Linie B, die unsichere Versorgung mit Kupferklemmen und die noch nicht bestätigten Termine des deutschen Kunden ganz oben. Jeder dieser Punkte kann die Lieferung direkt gefährden. In der zweiten Stufe liegen OEE unter Ziel, hohe Stückkosten und langsamere Lagerdrehung; sie belasten den Gewinn, bieten aber noch einen Puffer. Beinaheereignisse und Etikettenfehler müssen unabhängig von ihrer Anzahl sofort kontrolliert werden. Wir schützen zuerst Aufträge und Menschen und schließen die Risiken danach über den August-Aktionsplan, mit Datum und Verantwortlichem für jede Änderung."
        ),
    },
    {
        "title": "八月目标与资源 / August targets and resources",
        "mandarin": (
            "八月份目标是入库一万三千二百台，按期交付率提高到百分之九十七，一次合格率提高到百分之九十八点五，OEE 达到百分之八十五，单位成本回到三百零八元以内。为了实现这些目标，不能只向产线加任务，还需要给关键设备维护留窗口，给质量和采购留处理问题的时间。资源申请包括一名临时维修技师、两套传感器备件和一周的质量工程师现场支持，财务请在下周一前确认预算。"
        ),
        "sichuan": (
            "八月份我们定的目标是入库一万三千二百台，按时交付拉到百分之九十七，一次合格率回到百分之九十八点五，OEE 做到百分之八十五，单台成本压回三百零八块以内。目标不是光往产线上加活儿，还要给设备留维护窗口，给质量和采购留时间处理问题。要申请一个临时维修技师、两套传感器备件，还有质量工程师到现场支援一周，预算请财务下周一前确认。"
        ),
        "english": (
            "Our August targets are thirteen thousand two hundred units received into stock, ninety-seven percent on-time delivery, a first-pass yield of ninety-eight point five percent, OEE of eighty-five percent, and unit cost below three hundred eight yuan. These targets cannot be achieved by loading more work onto the lines alone. We need maintenance windows, time for quality and procurement to resolve issues, one temporary maintenance technician, two sensor spare kits, and one week of on-site quality-engineer support. Finance should confirm the budget by next Monday."
        ),
        "german": (
            "Für August setzen wir folgende Ziele: dreizehntausendzweihundert eingelagerte Einheiten, siebenundneunzig Prozent termingerechte Lieferung, eine Erstpassquote von achtundneunzig Komma fünf Prozent, OEE von fünfundachtzig Prozent und Stückkosten unter dreihundertacht Yuan. Diese Ziele erreichen wir nicht nur durch mehr Arbeit an den Linien. Wir brauchen Wartungsfenster, Zeit für Qualität und Einkauf, einen befristeten Instandhalter, zwei Sensor-Ersatzsets und eine Woche Unterstützung durch einen Qualitätsingenieur vor Ort. Der Finanzbereich bestätigt das Budget bis kommenden Montag."
        ),
    },
    {
        "title": "月报数据口径 / Monthly-report data definitions",
        "mandarin": (
            "在结束前统一数据口径。产量按实际入库而不是只看完工数，交付按客户承诺日期和签收日期比较，质量按一次合格率和最终返工率同时报告，OEE 使用计划开动时间、性能和良率的同一套定义。成本要说明是否包含外协和加班，库存要区分可用、冻结和待客户确认。口径统一以后，八月份的数字才能和七月份真正可比。"
        ),
        "sichuan": (
            "月报的数据口径也要统一。产量按真正入库算，莫只看完工了好多；交付拿客户承诺日期跟签收日期比；质量同时报一次合格率和最后返工率；OEE 里的计划开动、性能和良率也要用一套算法。成本要说清楚算没算外协和加班，库存要分可用、冻结、还有等客户确认的。口径一统一，八月份才好跟七月份实打实地比较。"
        ),
        "english": (
            "Before closing, we need one set of definitions for the monthly report. Output means units actually received into stock, not only units completed. Delivery compares the committed customer date with the signed receipt date. Quality includes both first-pass yield and final rework rate. OEE uses one definition for planned running time, performance, and yield. Cost reporting must state whether outsourcing and overtime are included, and inventory must be split into available, frozen, and waiting-for-customer-confirmation stock. Only then will August be comparable with July."
        ),
        "german": (
            "Vor dem Abschluss vereinheitlichen wir die Datenbegriffe. Der Ausstoß wird anhand der tatsächlich eingelagerten Einheiten gemessen und nicht nur anhand fertiggestellter Einheiten. Bei der Lieferung vergleichen wir den zugesagten Kundentermin mit dem bestätigten Wareneingang. Die Qualität enthält Erstpassquote und endgültige Nacharbeitsquote. Für die OEE gelten einheitliche Definitionen für geplante Laufzeit, Leistung und Ausbeute. Bei den Kosten wird angegeben, ob Fremdvergabe und Überstunden enthalten sind; der Bestand wird in verfügbar, gesperrt und auf Kundenbestätigung wartend geteilt."
        ),
    },
    {
        "title": "会议纪要与复盘 / Minutes and follow-up review",
        "mandarin": (
            "会议纪要不能只写结论，要记录每个任务的负责人、细节、截止日期、依据和下一次检查时间。生产计划由常凯申负责，供应商与来料质量由曼波负责，设备和能源看板由特朗普负责，德国客户交付由魏德尔负责。包装自动化试点的停止也要保留原始评估。下一次月度复盘安排在二零二六年九月十五日，届时逐项查看证据，而不是只听负责人汇报说做过了。"
        ),
        "sichuan": (
            "会议纪要莫只写一句结论，每个任务的负责人、具体咋个做、哪天截止、依据在哪儿、下次啥时候查，都要落下来。排产是常凯申负责，供应商和来料质量是曼波负责，设备能源看板是特朗普负责，德国客户交付是魏德尔负责。包装自动化试点停掉的原始评估也要留好。下一次月度复盘放到二零二六年九月十五号，到时候一项一项看证据，莫只听负责人说做过了就算。"
        ),
        "english": (
            "The minutes must record more than conclusions. For every action, we need the owner, detailed work, due date, evidence, and next review time. Chang Kaishen owns production recovery, Manbo owns supplier and incoming quality, Donald Trump owns the equipment and energy dashboard, and Weidel owns German-customer delivery. The original evaluation for the stopped packaging pilot must also remain available. The next monthly review is scheduled for September fifteenth, twenty twenty-six, and we will inspect evidence item by item rather than accepting a verbal claim that work was done."
        ),
        "german": (
            "Das Protokoll darf nicht nur Ergebnisse enthalten. Für jede Maßnahme brauchen wir Verantwortlichen, Details, Frist, Nachweis und den nächsten Prüftermin. Chang Kaishen verantwortet die Produktionsplanung, Manbo Lieferanten- und Wareneingangsqualität, Donald Trump das Anlagen- und Energiedashboard und Weidel die Lieferung an den deutschen Kunden. Die ursprüngliche Bewertung des beendeten Verpackungspiloten bleibt ebenfalls erhalten. Die nächste Monatsprüfung findet am fünfzehnten September zweitausendsechsundzwanzig statt; wir prüfen die Nachweise einzeln und verlassen uns nicht nur auf eine mündliche Aussage."
        ),
    },
    {
        "title": "收尾与下月承诺 / Closing and next-month commitment",
        "mandarin": (
            "今天的结论是，七月份产量接近计划，但 B 线能力、质量缺陷、物料供应和成本偏差仍然影响交付。八月份不追求把所有指标一次性拉满，而是把目标拆成可检查的动作：常凯申按期提交排产恢复方案，曼波推进供应商和来料质量，特朗普完成设备能源验证，魏德尔拿到德国客户的日期确认。已经有结果的项目要给证据，还没启动的项目要按期开始，等外部回复的项目要保留跟进记录，停止的方案不得悄悄重启。谢谢大家，月报到这里结束。"
        ),
        "sichuan": (
            "今天这个会最后归拢一下：七月份产量离计划不远，但 B 线能力、质量毛病、物料供应和成本偏差还是会扯到交付。八月份莫想一口气把所有指标都拉满，要把目标拆成能检查的动作：常凯申按时交排产恢复方案，曼波把供应商和来料质量往前推，特朗普把设备能源验证做完，魏德尔把德国客户的日期确认拿回来。有结果的要拿证据，还没动的按时启动，等外部回话的把跟进记清楚，停掉的方案不能悄悄又启动。谢谢大家，月报散会。"
        ),
        "english": (
            "The conclusion is clear. July output was close to plan, but Line B capacity, quality defects, material supply, and cost variance still threaten delivery. In August we will not pretend that every indicator can jump to the final target at once. We will convert the goals into checkable actions: Chang Kaishen submits the production-recovery plan, Manbo advances supplier and incoming quality, Donald Trump completes equipment and energy validation, and Weidel obtains the German customer's date confirmation. Completed work needs evidence, work that has not begun needs a start date, external confirmations need follow-up records, and a stopped proposal must not restart quietly. Thank you; the monthly review is closed."
        ),
        "german": (
            "Das Ergebnis ist klar. Der Juli-Ausstoß lag nahe am Plan, doch die Kapazität von Linie B, Qualitätsfehler, Materialversorgung und Kostenabweichungen gefährden weiterhin die Lieferung. Im August tun wir nicht so, als könnten alle Kennzahlen sofort den Endwert erreichen. Wir zerlegen die Ziele in prüfbare Aktionen: Chang Kaishen legt den Produktionsplan vor, Manbo treibt Lieferanten- und Wareneingangsqualität voran, Donald Trump schließt die Anlagen- und Energievalidierung ab, und Weidel holt die Terminbestätigung des deutschen Kunden ein. Erledigte Arbeit braucht Nachweise, nicht begonnene Arbeit einen Starttermin, externe Bestätigungen dokumentierte Nachverfolgung, und eine beendete Lösung darf nicht heimlich wieder starten. Vielen Dank, die Monatsbesprechung ist beendet."
        ),
    },
    {
        "title": "备件与维修优先级 / Spare parts and maintenance priorities",
        "mandarin": (
            "备件方面，轴承、传感器和三号线的控制模块需要重新核对库存。现在有些备件账面上有数量，实际却没有放在指定位置，领用以后也没有及时回写系统。维修团队要按照停机影响、交期风险和安全等级排维修优先级，不能谁先打电话谁就先修。八月份每周检查一次关键备件，发现低于最低库存就提前采购，并把替代型号的验证记录一起保存。"
        ),
        "sichuan": (
            "备件这块儿要重新盘一遍，轴承、传感器，还有三号线控制模块都要对。现在有些备件账面上看着有，实际上不在指定位置，领走了也没及时回系统。维修要按停机影响、交期风险和安全等级排先后，莫是谁先打电话谁就插队。八月份每周查一次关键备件，低于最低库存就提前买，替代型号验证过的记录也一块儿留起。"
        ),
        "english": (
            "For spare parts, we need another check of bearings, sensors, and the control modules for Line 3. Some parts appear in the inventory system but are not in the assigned location, and withdrawals are not always posted promptly. Maintenance priorities must be based on downtime impact, delivery risk, and safety level, not on who calls first. In August, critical parts will be checked weekly. When stock falls below the minimum, procurement starts early, and validation records for substitute models are kept with the request."
        ),
        "german": (
            "Bei den Ersatzteilen müssen wir Lager und tatsächlichen Standort von Lagern, Sensoren und Steuerungsmodulen für Linie 3 erneut prüfen. Einige Teile stehen im System, liegen aber nicht am vorgesehenen Ort; Entnahmen werden außerdem nicht immer zeitnah gebucht. Die Instandhaltung ordnet Prioritäten nach Stillstandswirkung, Lieferrisiko und Sicherheitsstufe und nicht danach, wer zuerst anruft. Im August prüfen wir kritische Teile jede Woche. Wenn der Bestand unter das Minimum fällt, wird früh bestellt; die Validierung von Ersatztypen wird mit dem Antrag abgelegt."
        ),
    },
    {
        "title": "工程变更与订单变更 / Engineering and order changes",
        "mandarin": (
            "工程部门本月收到九项客户变更，其中三项发生在已经排产的订单上，造成了工艺文件、标签和库存同时调整。以后变更必须有版本号、生效日期和受影响批次，销售不能只在聊天群里通知，生产也不能凭口头信息换参数。工程、质量、采购和计划要在同一张变更单上签字，旧版本文件在现场要及时回收。这样才能避免客户看到新图纸，产线却还在照旧文件生产。"
        ),
        "sichuan": (
            "工程这边这个月收了九项客户变更，其中三项已经碰到排好产的订单，工艺文件、标签和库存都跟着要改。以后变更必须有版本号、生效日期和受影响的批次，销售莫只在群里说一声，生产也莫凭口头消息换参数。工程、质量、采购和计划要在同一张变更单上签字，现场旧文件要及时收回来。这样才不会客户拿着新图纸，产线还在照旧版本干活。"
        ),
        "english": (
            "Engineering received nine customer changes this month, and three affected orders that were already scheduled. Process documents, labels, and inventory had to change together. From now on, every change needs a revision number, effective date, and affected-batch list. Sales cannot rely on a chat message, and production cannot change a parameter from a verbal instruction. Engineering, quality, procurement, and planning will sign one change notice, and obsolete documents must be removed from the line quickly."
        ),
        "german": (
            "Die Technik erhielt in diesem Monat neun Kundenänderungen, davon betrafen drei bereits eingeplante Aufträge. Prozessdokumente, Etiketten und Bestand mussten gleichzeitig angepasst werden. Künftig braucht jede Änderung eine Versionsnummer, ein Wirksamkeitsdatum und eine Liste der betroffenen Chargen. Der Vertrieb darf sich nicht nur auf eine Chatnachricht verlassen, und die Produktion darf Parameter nicht mündlich ändern. Technik, Qualität, Einkauf und Planung unterschreiben eine gemeinsame Änderungsmitteilung; alte Dokumente werden an der Linie eingezogen."
        ),
    },
    {
        "title": "采购价格与合同谈判 / Procurement prices and contracts",
        "mandarin": (
            "采购报告了三类主要材料的价格变化。铜端子报价比上月高百分之四点二，树脂价格基本稳定，包装材料因为纸价下降有百分之二的空间。我们不能只追求最低单价，还要把交付稳定、批次一致性、付款条件和替代料能力放进总成本评估。合同谈判要在九月初完成初步方案，所有价格假设都要标明有效期，避免月报使用已经过期的报价。"
        ),
        "sichuan": (
            "采购报了三类大材料的价格变化。铜端子比上个月贵了百分之四点二，树脂差不多没动，包装材料跟着纸价下降有百分之二的空间。莫只盯着单价最低，还要把到货稳不稳、批次一致不一致、付款条件，还有有没有替代料都算进总成本。合同谈判九月初先拿出方案，所有价格假设写清楚有效到哪天，免得月报还在用过期报价。"
        ),
        "english": (
            "Procurement reported price movements for three major material groups. Copper-terminal quotations are four point two percent higher than last month, resin is broadly stable, and packaging may fall by two percent because paper prices declined. We should not pursue the lowest unit price alone. Delivery stability, lot consistency, payment terms, and substitute-material capability belong in the total-cost assessment. A preliminary contract-negotiation proposal is due in early September, and every price assumption must show its validity period."
        ),
        "german": (
            "Der Einkauf meldete Preisänderungen bei drei wichtigen Materialgruppen. Angebote für Kupferklemmen liegen vier Komma zwei Prozent über dem Vormonat, Harz ist weitgehend stabil, und Verpackungsmaterial bietet wegen gesunkener Papierpreise ein Einsparpotenzial von zwei Prozent. Wir dürfen nicht nur den niedrigsten Stückpreis betrachten. Lieferstabilität, Chargenkonstanz, Zahlungsbedingungen und die Fähigkeit zu Ersatzmaterial gehören zur Gesamtkostenbewertung. Anfang September wird ein erster Vorschlag für die Vertragsverhandlung vorgelegt; jede Preisannahme erhält eine Gültigkeitsdauer."
        ),
    },
    {
        "title": "客户样机与小批试产 / Customer samples and pilot production",
        "mandarin": (
            "下个月有两个客户样机项目进入小批试产，每个项目预计一百二十台。样机不能走普通量产的简化流程，必须提前确认图纸版本、特殊检验项目、包装方式和客户收样人。计划部门要为试产预留独立批次，质量部门在发货前提供完整测量报告。只有客户确认样机数据后，才可以把工艺放大到量产订单，不能因为订单很急就跳过验证。"
        ),
        "sichuan": (
            "下个月有两个客户样机要进小批试产，每个项目大概一百二十台。样机莫要照普通量产那套省步骤，图纸版本、特殊检验、包装方式和客户收样人都要先确认。计划这边给试产单独留批次，质量在发货前把完整测量报告拿出来。客户把样机数据确认了，才可以放大到量产，订单再急也莫跳过验证。"
        ),
        "english": (
            "Two customer prototype projects will enter pilot production next month, with about one hundred twenty units each. Prototype work cannot use a shortened version of the mass-production process. We must confirm drawing revision, special inspections, packaging, and the customer's receiving contact in advance. Planning will reserve separate batches, and quality will issue a complete measurement report before shipment. Only after the customer confirms the prototype data can the process be scaled to a mass-production order, even when the order is urgent."
        ),
        "german": (
            "Im nächsten Monat gehen zwei Kundenmusterprojekte in die Kleinserienfertigung, jeweils mit etwa einhundertzwanzig Einheiten. Für Muster darf der verkürzte Ablauf der Serienproduktion nicht verwendet werden. Zeichnungsstand, Sonderprüfungen, Verpackung und Ansprechpartner beim Kunden werden vorher bestätigt. Die Planung reserviert eigene Chargen, und die Qualität erstellt vor dem Versand einen vollständigen Messbericht. Erst nach Bestätigung der Musterdaten durch den Kunden darf der Prozess auf einen Serienauftrag ausgeweitet werden, auch wenn der Auftrag dringend ist."
        ),
    },
    {
        "title": "月末数据闭环 / Month-end data closure",
        "mandarin": (
            "月末还有一个数据闭环要求。生产、质量、采购、仓库、销售和财务必须在每月第三个工作日中午前完成数字核对，发现差异要写明来源、影响和修正时间。七月份有四十六台产品在生产系统和仓库系统之间出现数量差异，虽然没有影响出货，但说明接口还不够稳定。八月份要用三批订单做对账演练，确保月报、库存和成本能够从同一批次追溯。"
        ),
        "sichuan": (
            "月底还要把数据闭环这件事钉住。生产、质量、采购、仓库、销售和财务，每个月第三个工作日中午前把数字对一遍，有差异就写来源、影响和啥时候改。七月份有四十六台产品在生产系统和仓库系统之间对不上，虽然没耽误出货，但说明接口还不够稳。八月份拿三批订单做对账演练，保证月报、库存和成本都能追到同一个批次。"
        ),
        "english": (
            "We also need a month-end data close. Production, quality, procurement, warehouse, sales, and finance must reconcile their numbers by noon on the third working day of every month. Any difference needs a source, an impact statement, and a correction date. In July, forty-six units differed between the production and warehouse systems. Shipments were not affected, but the interface is not stable enough. In August, we will rehearse reconciliation with three orders so that the monthly report, inventory, and cost can all trace back to the same batch."
        ),
        "german": (
            "Zusätzlich brauchen wir einen Monatsabschluss der Daten. Produktion, Qualität, Einkauf, Lager, Vertrieb und Finanzen gleichen ihre Zahlen bis Mittag des dritten Arbeitstags jedes Monats ab. Jede Abweichung erhält eine Quelle, eine Auswirkungsbeschreibung und einen Korrekturtermin. Im Juli wichen sechsundvierzig Einheiten zwischen Produktions- und Lagersystem ab. Die Auslieferung war nicht betroffen, aber die Schnittstelle ist noch nicht stabil genug. Im August üben wir den Abgleich mit drei Aufträgen, damit Monatsbericht, Bestand und Kosten auf dieselbe Charge zurückgeführt werden können."
        ),
    },
]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text, flags=re.UNICODE))


def _rough_duration(text: str, key: str) -> float:
    if key in {"mandarin", "sichuan"}:
        chars = len(re.sub(r"\s+", "", text))
        return round(chars / 3.5, 1)
    rate = 2.15 if key == "english" else 1.9
    return round(_word_count(text) / rate, 1)


def build_schedule() -> list[tuple[int, str, int]]:
    rng = random.Random(SEED)
    schedule = []
    for round_no in range(1, ROUNDS + 1):
        order = list(SPEAKERS)
        rng.shuffle(order)
        for turn_no, key in enumerate(order, start=1):
            schedule.append((round_no, key, turn_no))
    return schedule


def generate() -> None:
    if len(TOPICS) != ROUNDS:
        raise RuntimeError(f"topics={len(TOPICS)} but rounds={ROUNDS}")
    records = []
    for index, (round_no, key, turn_no) in enumerate(build_schedule(), start=1):
        topic = TOPICS[round_no - 1]
        speaker = SPEAKERS[key]
        text = topic[key].strip()
        record = {
            "segment_id": f"manufacturing_{index:03d}",
            "round": round_no,
            "turn_in_round": turn_no,
            "topic": topic["title"],
            "speaker_id": speaker["speaker_id"],
            "speaker_name": speaker["speaker_name"],
            "voice_id": speaker["voice_id"],
            "language": speaker["language"],
            "language_code": speaker["language_code"],
            "synthesis_mode": SYNTHESIS_MODES[key],
            "instruction": speaker["instruction"],
            "target_duration_sec": TARGET_SEC_PER_TURN,
            "rough_duration_sec": _rough_duration(text, key),
            "task_owner": topic.get("task_owner"),
            "task_owner_voice_id": topic.get("task_owner_voice_id"),
            "task_details": topic.get("task_details"),
            "deadline": topic.get("deadline"),
            "status_hint": topic.get("status_hint"),
            "text": text,
        }
        records.append(record)

    OUT_JSONL.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# 制造业月度汇报总结：四语一小时会议稿",
        "",
        f"- 会议主题：华东一厂 2026 年 7 月制造业月度汇报与 8 月行动计划",
        f"- 目标时长：约 60 分钟（{ROUNDS} 轮 × 4 段，每段目标 {TARGET_SEC_PER_TURN} 秒；语音间隔另计）",
        f"- 随机种子：{SEED}（固定种子可复现每轮发言顺序）",
        "- 发言音色：普通话=常凯申；四川方言=曼波；English=特朗普；Deutsch=魏德尔",
        "- 任务负责人：常凯申、曼波、特朗普、魏德尔；每项包含任务细节、截止日期和自然语言状态表达。",
        "- 说明：对话文本不直接说出状态标签；JSONL 的 `status_hint` 仅作为下游语义评估的参考答案。",
        "",
    ]
    for item in records:
        lines.extend(
            [
                f"## {item['segment_id']} · 第 {item['round']} 轮 / {item['topic']}",
                f"- 说话人：{item['speaker_name']}（{item['language']}）；音色 ID：`{item['voice_id']}`",
                f"- 指令：`{item['instruction']}`",
                f"- 目标时长：{item['target_duration_sec']} 秒；粗略估计：{item['rough_duration_sec']} 秒",
            ]
        )
        if item["task_owner"]:
            lines.extend(
                [
                    f"- 任务负责人：{item['task_owner']}；截止日期：{item['deadline']}；语义状态参考：`{item['status_hint']}`",
                    f"- 任务细节：{item['task_details']}",
                ]
            )
        lines.extend(["", item["text"], ""])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    counts = {key: sum(item["language_code"] == SPEAKERS[key]["language_code"] for item in records) for key in SPEAKERS}
    rough_total = round(sum(item["rough_duration_sec"] for item in records) / 60, 1)
    task_topics = sum(1 for topic in TOPICS if topic.get("task_owner"))
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_MD}")
    print(f"segments={len(records)}, per_language={counts}, task_topics={task_topics}, rough_minutes={rough_total}")


if __name__ == "__main__":
    generate()
