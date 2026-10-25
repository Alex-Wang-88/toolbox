"""Generate a freer, shorter manufacturing monthly meeting manuscript.

The meeting is intentionally not a fixed four-speaker round-robin.  It has
three report turns, targeted boss questions and answers, followed by next-
month action assignments.  This script creates manuscript files only.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from generate_manufacturing_role_meeting import (
    SPEAKERS as BASE_SPEAKERS,
    rough_duration,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "test_inputs" / "manufacturing_role_meeting_15m_v3.jsonl"
OUT_MD = ROOT / "test_inputs" / "manufacturing_role_meeting_15m_v3.md"
TARGET_SEC_PER_SEGMENT = 45


SPEAKERS = copy.deepcopy(BASE_SPEAKERS)
SPEAKERS["operations"].update(
    {
        "voice_id": "clone_sichuan_dialect",
        "instruction": "请用四川话口语表达，保留四川方言词汇和语气，不要转成普通话。",
    }
)


TASKS = {
    "production": {
        "owner": "曼波",
        "owner_role": "生产与设备经理",
        "details": "根据 A、B、C 三条线的真实能力，提交十天恢复排产表，包含班次、换型窗口、维护窗口、加班上限、物料替代、每日偏差和责任人。",
        "deadline": "2026-09-03",
        "status_hint": "未开始",
    },
    "customer": {
        "owner": "特朗普",
        "owner_role": "销售与客户平台主管",
        "details": "确认九月预测、六百二十台积压订单的交付窗口和五个最高风险客户的替代方案，形成每周三更新的客户承诺表。",
        "deadline": "2026-09-05",
        "status_hint": "进行中",
    },
    "cost": {
        "owner": "魏德尔",
        "owner_role": "财务、采购与合规负责人",
        "details": "提交关键供应商价格、双供应商覆盖、付款条件、库存现金占用和每台节省八元机会的综合方案，并标出未确认信息。",
        "deadline": "2026-09-08",
        "status_hint": "待确认",
    },
}


# The sequence is deliberately uneven: the three initial reports are followed
# by questions to the relevant person, and only then by next-month actions.
# Sichuan lines use dialect orthography instead of Mandarin text plus a style
# instruction.  English and German remain natural meeting-language turns.
SEGMENTS = [
    {
        "round": 1,
        "phase": "开场",
        "speaker": "boss",
        "topic": "七月经营月会开场",
        "text": "今天把七月份的经营结果过一遍，先听汇报，后面再问问题和定下个月的动作。整体看，计划产量一万二千八百台，实际入库一万二千四百八十台，少了三百二十台；按期交付率百分之九十四点六，离目标还有差距。质量一次合格率百分之九十八点一，客户投诉十二起，库存周转三十八天，单位成本比预算高十三元。大家不要把数字重新念一遍，直接讲差异、原因和需要协调的地方。先生产，再销售，最后财务采购。",
    },
    {
        "round": 2,
        "phase": "部门汇报",
        "speaker": "operations",
        "topic": "生产与设备汇报",
        "text": "现场这本账我先摆出来。A 线完成得还算过得去，有百分之九十九；B 线只有百分之九十四，C 线在百分之九十六，差额主要卡在 B 线晚班和三号线短停。晚班少跑了二十六个小时，平均换型四十二分钟，十分钟以内的小停有四十六回，加起来接近八个小时。质量这边，虚焊、外壳划伤和连接器没得装到位，返工率涨到百分之一点七。缺料停线有四次，铜端子和树脂批次最恼火。设备、物料、换型和人手是拴在一根绳上的，哪个环节没整安逸，产量就要掉。",
    },
    {
        "round": 2,
        "phase": "部门汇报",
        "speaker": "sales",
        "topic": "销售与客户汇报",
        "text": "From the customer side, the order pool is thirteen thousand two hundred units. Six hundred twenty units are already inside next month’s delivery window, and thirty-seven orders have a credible delay risk. The most sensitive accounts are waiting for firm dates on two products that use copper terminals. Four German customers are asking for traceability and a clear corrective-action date after the recent complaints. I have separated firm orders, probable demand, and orders waiting for specification confirmation. The main problem is that our customer promises are still being updated faster than the plant can confirm capacity.",
    },
    {
        "round": 2,
        "phase": "部门汇报",
        "speaker": "finance",
        "topic": "财务、采购与合规汇报",
        "text": "Aus Finanz- und Einkaufssicht liegen die tatsächlichen Herstellungskosten bei dreihundertachtzehn Yuan pro Einheit und damit dreizehn Yuan über dem Budget. Materialpreise erklären sechs Yuan, Nacharbeit und Ausschuss drei Yuan, Überstunden und Fremdvergabe weitere vier Yuan. Die Lagerreichweite ist von zweiunddreißig auf achtunddreißig Tage gestiegen, und die termingerechte Lieferquote der Lieferanten liegt bei zweiundneunzig Komma acht Prozent. Bei Kupferklemmen fehlen noch zwei Angebote. Für die Kundenprüfung brauchen wir außerdem vollständige Nachweise zu Schulung, Energieverbrauch, Reklamationen und Lieferanten.",
    },
    {
        "round": 3,
        "phase": "老板提问",
        "speaker": "boss",
        "topic": "追问生产恢复优先级",
        "text": "曼波，刚才你讲了不少原因，我只追一个问题：下个月如果资源只能先解决一件事，你先动 B 线晚班、三号线短停，还是先处理缺料？你给我一个排序，也说清楚前三天能看到什么变化。不要回答‘都重要’，要按对交付的影响来排。",
    },
    {
        "round": 3,
        "phase": "员工回答",
        "speaker": "operations",
        "topic": "生产恢复优先级回答",
        "text": "我会先把 B 线晚班和三号线短停拢到一块儿处理，缺料同步盯，但不拿缺料当第一借口。头三天先把晚班换型从四十二分钟压到三十五分钟以内，再把三号线传感器误报警和参数没核准这两个毛病整掉；铜端子和树脂则按每天缺口列出来，能替代的先做小批验证。这样先把丢掉的有效工时捡回来，第四天再看产量、停机和返工有没有一起往好处走。要是数据没起色，我马上把问题拎出来，不等到月底才喊恼火。",
    },
    {
        "round": 4,
        "phase": "老板提问",
        "speaker": "boss",
        "topic": "追问客户承诺",
        "text": "特朗普，销售这边我不要求你把所有客户都说服。请你挑出最可能影响收入和信誉的两类客户：一类是必须保住日期的，一类是可以谈分批交付的。你会用什么依据跟客户谈？如果生产只给你九成产能，你先保哪一批？",
    },
    {
        "round": 4,
        "phase": "员工回答",
        "speaker": "sales",
        "topic": "客户承诺回答",
        "text": "I would protect customers with contractual penalties, launch commitments, and a high probability of repeat business. For the second group, I would propose staged delivery, but only with exact quantities and dates rather than a vague promise. If the plant gives me ninety percent of the theoretical capacity, I will reserve the first allocation for the two copper-terminal products and the customers already affected by a complaint. The remaining orders will be ranked by margin, contractual exposure, and whether the customer accepts a partial shipment. I need one approved capacity window from operations before I send any final date.",
    },
    {
        "round": 5,
        "phase": "老板提问",
        "speaker": "boss",
        "topic": "追问成本与现金",
        "text": "魏德尔，单位成本高十三元，库存又多了六天。你先告诉我哪些钱可以马上收回来，哪些钱不能为了好看硬砍。采购降价、减少返工、压缩加班和消化慢动料，哪两项最有把握在八月份见到结果？",
    },
    {
        "round": 5,
        "phase": "员工回答",
        "speaker": "finance",
        "topic": "成本与现金回答",
        "text": "Kurzfristig sehe ich die sicherste Wirkung bei Nacharbeit, ungeplanten Überstunden und langsam drehenden Beständen. Wir können die wiederholte Nacharbeit mit einer klaren Fehlerliste reduzieren und zusätzliche Schichten nur für bestätigte Aufträge freigeben. Bei Kupferklemmen darf der Preis nicht isoliert betrachtet werden; ein billiger Lieferant mit Verspätung erhöht Ausschuss, Expressfracht und Vertragsrisiko. Für August erstelle ich eine Kostenbrücke vom Budget zu den tatsächlichen Stückkosten und trenne sofort wirksame Maßnahmen von solchen, die erst nach einer Lieferantenbestätigung belastbar sind.",
    },
    {
        "round": 6,
        "phase": "老板提问",
        "speaker": "boss",
        "topic": "追问质量闭环",
        "text": "曼波，质量一次合格率只有百分之九十八点一，客户投诉和返工都在增加。现场现在最容易重复的缺陷是什么？下个月你准备在哪个工位先验证，什么时候能拿出第一批数据？我不需要再听‘加强巡检’，要听具体动作和判断标准。",
    },
    {
        "round": 6,
        "phase": "员工回答",
        "speaker": "operations",
        "topic": "质量闭环回答",
        "text": "现在最容易重复的是虚焊和连接器没装到位，换型以后首件确认没得做扎实，后头才一路带到终检。下个月先在 B 线把首件确认和前两小时巡检卡住，缺陷照片、班组、设备参数和批次拢到一起。第一天把基线数据立起，第三天看同类缺陷有没有往下掉；要是返工率还在百分之一点七附近，就把工艺参数和培训一起翻出来查。这个事情不是多贴一张表就算完，得拿数据看哪个动作真有用。",
    },
    {
        "round": 7,
        "phase": "交叉确认",
        "speaker": "boss",
        "topic": "交叉确认分批交付方案",
        "text": "那就把现场和客户放到一起确认。曼波，如果销售把一部分订单改成分批发货，生产需要客户最晚什么时候给最终优先级？特朗普，如果客户不接受分批，你要在什么时候把风险推回来，不能等到发货前一天。你们两个直接给出时间，不用再等下一轮月会。",
    },
    {
        "round": 7,
        "phase": "员工回答",
        "speaker": "operations",
        "topic": "分批交付的现场条件",
        "text": "销售最迟每周三下午把优先级摆过来，特别是铜端子那两款，不然排产一变，换型和物料就要跟着打乱。要是客户同意分批，我这边先保关键批次，再把低批量的货集中到周二、周四换型。现场需要的是一张锁过的清单，莫今天说 A 先、明天又把 B 插到前头；优先级一旦定下来，三条线才好按真实能力排，不会靠临时加班遮问题。",
    },
    {
        "round": 7,
        "phase": "员工回答",
        "speaker": "sales",
        "topic": "客户风险回传机制",
        "text": "I can return any unresolved delivery risk within forty-eight hours after the capacity window is updated. For a customer who refuses staged delivery, I will present the risk before confirming the date and ask for a written decision on priority or additional freight. I will also keep the customer evidence in the same table as the production assumption. That way, a change in capacity or specification is visible immediately instead of becoming a surprise at the end of the month.",
    },
    {
        "round": 8,
        "phase": "下月计划",
        "speaker": "boss",
        "topic": "指定生产与设备计划",
        "task": "production",
        "text": "下个月生产这边就定一项主计划：曼波牵头做十天恢复排产。表里要有 A、B、C 三条线的班次、换型和保养窗口，也要写清楚物料替代、加班上限、每天看什么偏差。九月三日前给我第一版。现在这件事还没有正式开始，今天下午先把数据拉出来，明天把班组和维修叫到一起，不要先做漂亮模板。",
    },
    {
        "round": 8,
        "phase": "员工确认",
        "speaker": "operations",
        "topic": "生产与设备计划确认",
        "task": "production",
        "text": "好，这个安排我接起。今天下午我先把三条线的产量、短停、换型、维护和缺料数据拢齐，明天上午跟班组和维修把能不能做到算清楚。九月三号前我交排产表、风险清单和每天的跟踪格式，后头每天照偏差调整。这个事情现在还没正式动手，但启动时间已经定了，哪个地方卡住我当天就报。",
    },
    {
        "round": 9,
        "phase": "下月计划",
        "speaker": "boss",
        "topic": "指定销售与客户计划",
        "task": "customer",
        "text": "客户这边由特朗普牵头做承诺表。把九月预测、六百二十台积压订单、五个最高风险客户和分批交付选项放在一张表里，每周三更新，九月五日前给出可追溯版本。你已经在联系客户了，但最终日期还没全部锁住，所以邮件、电话纪要和客户确认都要留档，不能把客户一句‘应该可以’写成确定承诺。",
    },
    {
        "round": 9,
        "phase": "员工确认",
        "speaker": "sales",
        "topic": "销售与客户计划确认",
        "task": "customer",
        "text": "Understood. The customer work is already in progress. I have separated firm orders from forecast demand and started contacting the five highest-risk accounts. By September fifth, I will deliver the commitment table with exact dates, staged-shipment options, customer evidence, and one alternative for each high-risk account. I will not mark a date as confirmed until the customer replies in writing or the decision is recorded in a call note.",
    },
    {
        "round": 10,
        "phase": "下月计划",
        "speaker": "boss",
        "topic": "指定采购、成本与合规计划",
        "task": "cost",
        "text": "财务采购这边由魏德尔牵头做综合方案。关键供应商报价、第二来源、付款条件、库存占用和每台节省八元的机会要放在一起，九月八日前交。现在有两家供应商的报价和付款条件还没回来，这部分只能先标成等待确认，但模板、口径和已知数据今天就可以先搭起来。方案不能只看采购单价，还要算停线、返工、运输和现金占用。",
    },
    {
        "round": 10,
        "phase": "员工确认",
        "speaker": "finance",
        "topic": "采购、成本与合规计划确认",
        "task": "cost",
        "text": "Verstanden. Ich beginne heute mit dem Vergleichsmodell und markiere alle Informationen, die von Lieferanten oder Vertrieb noch bestätigt werden müssen. Die Bewertung enthält Preis, Zahlungsziel, Sicherheitsbestand, Ausschuss, Logistik und Cash-Bindung. Bis zum achten September liefere ich eine gemeinsame Empfehlung für jeden kritischen Lieferanten. Die Lösung ist heute noch nicht entscheidungsreif, aber die offenen Punkte und der nächste Prüftermin werden klar dokumentiert.",
    },
    {
        "round": 11,
        "phase": "收口",
        "speaker": "boss",
        "topic": "下月目标与会议收口",
        "text": "今天先到这里。下个月不再把四个人的汇报做成轮流念稿，周报只带差异、证据和需要决定的事项。曼波九月三日前交恢复排产，特朗普九月五日前交客户承诺表，魏德尔九月八日前交采购成本方案。八月份目标是入库一万三千二百台、按期交付率百分之九十七、一次合格率百分之九十八点五，库存周转先压回三十五天以内。质量一次合格率要回到百分之九十八点五以上，未遂事件和能源消耗也一起看，不能只盯产量。订单优先级周三锁定，产能窗口、客户回复和现金影响放在同一张表里，下一次会议先看这些动作有没有按约完成。没启动的事情要马上有动作，已经在做的事情要带证据，等外部回复的事情要标清楚。周一先看第一版数据，散会。",
    },
]


def _task_fields(item: dict) -> dict:
    task_key = item.get("task")
    if not task_key:
        return {
            "task_owner": None,
            "task_owner_role": None,
            "task_details": None,
            "deadline": None,
            "status_hint": None,
        }
    task = TASKS[task_key]
    return {
        "task_owner": task["owner"],
        "task_owner_role": task["owner_role"],
        "task_details": task["details"],
        "deadline": task["deadline"],
        "status_hint": task["status_hint"],
    }


def generate() -> None:
    records = []
    round_turns: dict[int, int] = {}
    for index, item in enumerate(SEGMENTS, start=1):
        speaker_key = item["speaker"]
        speaker = SPEAKERS[speaker_key]
        round_no = item["round"]
        round_turns[round_no] = round_turns.get(round_no, 0) + 1
        text = item["text"].strip()
        record = {
            "segment_id": f"role_meeting_v3_{index:03d}",
            "sequence": index,
            "round": round_no,
            "turn_in_round": round_turns[round_no],
            "phase": item["phase"],
            "topic": item["topic"],
            "speaker_id": speaker["speaker_id"],
            "speaker_name": speaker["speaker_name"],
            "role": speaker["role"],
            "is_boss": speaker["is_boss"],
            "voice_id": speaker["voice_id"],
            "language": speaker["language"],
            "language_code": speaker["language_code"],
            "synthesis_mode": speaker["synthesis_mode"],
            "instruction": speaker["instruction"],
            "target_duration_sec": TARGET_SEC_PER_SEGMENT,
            "rough_duration_sec": rough_duration(text, speaker_key),
            "text": text,
        }
        record.update(_task_fields(item))
        records.append(record)

    OUT_JSONL.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# 制造业月度管理会：自由问答版四语会议稿 v3",
        "",
        "- 目标：约 15–18 分钟的制造业月度管理会议",
        "- 流程：老板开场 → 生产、销售、财务分别汇报 → 老板针对问题提问 → 被问到的员工回答 → 确定下个月计划 → 收口",
        "- 结构：11 个会议阶段、22 段口播；各阶段按实际需要发言，不采用每轮四人固定轮流发言",
        "- 老板：常凯申，普通话；直接围绕结果、风险、问题和决定发言，不介绍自己的岗位职责",
        "- 生产与设备经理：曼波，四川方言；音色 ID：`clone_sichuan_dialect`",
        "- 四川方言台词使用四川话口语的音译式写法，技术指标和数字保持清楚",
        "- 销售与客户平台主管：特朗普，English",
        "- 财务、采购与合规负责人：魏德尔，Deutsch",
        "- 任务安排：生产、客户、采购成本三项任务均有负责人、细节和截止日期；`status_hint` 供语义评估参考",
        "",
    ]
    for item in records:
        lines.extend(
            [
                f"## {item['segment_id']} · 第 {item['round']} 阶段 / {item['phase']} / {item['topic']}",
                f"- 说话人：{item['speaker_name']}（{item['role']}）；音色：`{item['voice_id']}`",
                f"- 语言：{item['language']}；阶段内第 {item['turn_in_round']} 段；粗略估计：{item['rough_duration_sec']} 秒",
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

    counts = {
        key: sum(item["speaker_id"] == SPEAKERS[key]["speaker_id"] for item in records)
        for key in SPEAKERS
    }
    phase_counts = {}
    for item in records:
        phase_counts[item["phase"]] = phase_counts.get(item["phase"], 0) + 1
    rough_total = round(sum(item["rough_duration_sec"] for item in records) / 60, 1)
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_MD}")
    print(f"segments={len(records)}, per_role={counts}, phases={phase_counts}, rough_minutes={rough_total}")


if __name__ == "__main__":
    generate()
