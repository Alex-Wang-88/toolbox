"""Generate the revised role-based manufacturing meeting manuscript.

This version keeps the original draft intact while making the boss turns more
natural and replacing the operations turns with Sichuan-dialect orthography.
It creates manuscript files only; audio generation is intentionally separate.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from generate_manufacturing_role_meeting import (
    ROLE_ORDER,
    SPEAKERS as BASE_SPEAKERS,
    TARGET_SEC_PER_TURN,
    TOPICS as BASE_TOPICS,
    rough_duration,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "test_inputs" / "manufacturing_role_meeting_20m_v2.jsonl"
OUT_MD = ROOT / "test_inputs" / "manufacturing_role_meeting_20m_v2.md"
ROUNDS = 12


SPEAKERS = copy.deepcopy(BASE_SPEAKERS)
SPEAKERS["operations"].update(
    {
        "voice_id": "clone_sichuan_dialect",
        "instruction": "请用四川话口语表达，保留四川方言词汇和语气，不要转成普通话。",
    }
)


TOPIC_TITLES = [
    "七月经营结果 / July operating results",
    "产能与交付风险 / Capacity and delivery risk",
    "质量异常与客户投诉 / Quality issues and customer complaints",
    "设备效率与维修安排 / Equipment efficiency and maintenance",
    "物料齐套与库存 / Material availability and inventory",
    "成本差异与现金 / Cost variance and cash",
    "安全、环保与客户审核 / Safety, environment, and audits",
    "行动项：生产恢复排产 / Action: production recovery plan",
    "行动项：客户承诺表 / Action: customer commitment table",
    "行动项：采购与成本方案 / Action: procurement and cost plan",
    "经营复盘机制 / Operating review cadence",
    "结论与下月检查点 / Decisions and next checkpoints",
]


# The boss speaks as a manager making decisions and asking for evidence.  He
# does not introduce his own title or repeat the department boundaries.
BOSS_TEXTS = [
    "先看七月份结果。计划产量一万二千八百台，实际入库一万二千四百八十台，按期交付率百分之九十四点六。今天不按部门念稿，先把差异最大的几个点讲清楚。曼波先说现场，重点讲 B 线为什么没跟上；特朗普和魏德尔后面补客户与成本。",
    "产量、订单和交付要对得上。生产不能只报完成数量，销售也不能没有产能依据就给日期。先把高风险客户保住，再处理低批量换型。今天把每周更新的联动表定下来，月底再发现三套数字不一样，就说明管理没跟上。",
    "质量我不听“已经拦截”四个字就算结束。七月份一次合格率百分之九十八点一，客户投诉十二起，退货四起。现场缺陷和客户反馈要落到同一条闭环里，下个月质量指标和投诉一起看，别让销售一直替同一个问题解释。",
    "设备先别急着上新。现在 OEE 是百分之八十二点四，预防性维护完成率百分之八十八，已有设备的短停和保养还没做扎实。包装自动化方案回收期超过三十六个月，当前方案先停，后面要重新提就拿新的数据来。",
    "缺料停线四次，库存周转又从三十二天升到三十八天，这两个问题不能分开看。关键物料要有第二来源，慢动料要有消化计划。八月份把物料齐套率、库存天数和订单风险放进一张周报，红色项目必须带着动作来。",
    "单位成本超预算十三元，不能简单让所有部门平均分摊。材料、返工报废、加班外协各自查清楚，八月份目标是三百零八元以内，但不能拿安全和质量去换成本。财务把差异拆到责任动作上，月底我要看到改善对应的金额。",
    "安全和合规不能拿来换交付。七月份没有损失工时事故，但有两起未遂事件；能源消耗每台一点八二千瓦时，也高于一点七五的目标。没有受伤不等于没有风险，客户审核也不能临时补材料，月底把现场、客户和证据一起复盘。",
    "曼波，生产恢复这件事你会后先动起来。按 A、B、C 三条线的真实能力，做一张未来十天的排产表，班次、换型、维护、加班上限、缺料替代和每天的偏差都要写进去。九月三日前交我，不要先做漂亮的汇报，先把能不能做到算明白。这项工作现在还没起步，今天就要有第一版数据。",
    "特朗普，客户承诺表由你牵头。九月预测、六百二十台积压订单的交付窗口、五个最高风险客户的替代方案，都放进一张表里，每周三更新。九月五日前给我可追溯的版本，客户邮件、电话纪要和最终确认都留档。现在这项工作已经在往前走，但日期还没全部锁住，不能把猜测写成承诺。",
    "魏德尔，供应商和成本这块你牵头。关键供应商价格、双供应商覆盖、付款条件、库存占用和每台节省八元的机会，九月八日前做成一个综合方案。还有几家报价没回来，你先把模板和口径定好，外部信息没齐之前不要假装已经有结论。",
    "周一的经营复盘我来主持，先看订单、产能、质量、现金和合规五项，超过阈值就当场定资源，不留到月会再救火。模板已经搭起来了，九月一日前把第一次正式复盘跑通；后面每周都要留下数据和决定，不是开完会就算完成。",
    "今天定下来的几件事按日期落下去：九月三日前看生产恢复排产，九月五日前看客户承诺表，九月八日前看采购和成本方案，周一先把经营复盘跑起来。没动的事情要给启动时间，正在做的事情要拿证据，外部回复没齐的要留记录。八月份目标是入库一万三千二百台、按期交付率百分之九十七、一次合格率百分之九十八点五，散会。",
]


# These are deliberately written in Sichuan-dialect orthography rather than
# standard Mandarin sentences followed by a dialect instruction.  Technical
# terms and figures remain explicit so downstream semantic evaluation stays
# reliable.
OPERATIONS_TEXTS = [
    "我先把现场这本账摆一哈。A 线完成得还算过得去，有百分之九十九；B 线只有百分之九十四，C 线也在百分之九十六。B 线这个瓶颈最恼火，晚班少跑了二十六个小时，三号线小停也多。后头我把产量、停机、换型和人手分开说，哪个环节拉胯就说哪个，莫把啥子差额都往订单上甩。",
    "B 线晚班不是单纯人手少，是换型等得久、小停又多。平均换型要四十二分钟，三号线十分钟以内的小停有四十六回，加起来快八个小时了。我们可以把小批量的货集中到周二、周四换型，高风险订单锁到白班；但是销售要先把优先级摆清楚，财务也要把加班的上限说死。",
    "现场最恼火的是虚焊、外壳划伤，还有连接器没得装到位。返工率涨到百分之一点七，不是哪个工位单独背锅，换型过后的首件确认也没整安逸。我们准备把缺陷照片、班组、设备参数和批次拢到一块儿，先在 B 线把巡检加密，莫只靠最后一道检验来兜底。",
    "三号线老是短停，主要是传感器误报警、换型以后参数没核准，还有些小毛病反复来。维修班天天被突发故障牵着鼻子走，就没得空做预防保养。关键保养窗口要塞进排产表，轴承、皮带和传感器也要留最低库存。设备先整稳当了，再谈扩产才有底气。",
    "物料这块最扯的是铜端子和树脂批次。缺料了产线要停，来料有毛病又要返工，两个事情叠到一起就更恼火。仓库头把可用料、冻结料和等客户确认的半成品分开摆，替代料先做小批验证，莫临时换上去才发现工艺吃不消。",
    "生产这边能直接扛的是返工、报废、换型损失和加班安排。我们把每条线的损失按工序拆开，先查重复返工和没计划的空等。订单优先级早点摆出来，周末加班可以少些；要是临时插单一窝蜂，现场就会拿加班来遮问题，这个不得行。",
    "前头两起未遂，一个是叉车倒车看不清，一个是维修时还有残余压力。下个月我把叉车路线、挂牌上锁和高温设备操作重新培训，班组长每天开工前花三分钟把风险过一遍。能源这块，烘箱空转和压缩空气漏气比较明显，巡检要写进点检表，莫只靠嘴巴提醒。",
    "收到。这个排产表不能只把产量数字填起，换型、保养、物料和加班都要算进去。今天下午我先把三条线的实际数据拢齐，明天上午把班组和维修叫来碰一下，后头每天照着偏差调整。九月三号前我交排产表、风险清单和跟踪格式，哪天掉了链子也要说得明明白白。",
    "我给销售把产能窗口摆清楚，特别是 B 线和三号线哪天要保养、哪段时间只能跑多少。客户要是同意分批发货，生产可以先把关键批次保住；客户非要一次交齐，就要提前看设备和物料扛不扛得住。这样给出去的日期才不是拍脑壳定的。",
    "采购要换料的话，现场先做小批验证。单看单价便宜不一定巴适，缺料停线和来料返工的代价也要算进去。我会把物料消耗、替代料验证和停线影响数据给魏德尔，价格方案出来以后一起看，莫把新料直接推到量产线上去试胆量。",
    "每周复盘要是只念数字，就没得啥子用。生产这边把产能、停机、质量和人手一块儿带过去，B 线到底先修还是先加班，要看订单和现金咋个取舍。后头我们不再只报‘这周做了好多’，要把差异、原因和下一步都摆出来，大家才好下决定。",
    "生产这边按九月三号交表，产能、换型、保养、物料和加班一项都不漏。只要销售把优先级早点说清楚，财务把成本边界摆明白，现场就不靠临时加班来遮问题。八月份我每天看偏差，哪个地方没得安逸就马上拎出来，不等到月底才喊恼火。",
]


def generate() -> None:
    if len(BASE_TOPICS) != ROUNDS or len(BOSS_TEXTS) != ROUNDS or len(OPERATIONS_TEXTS) != ROUNDS:
        raise RuntimeError("base topics and revised text arrays must all contain 12 rounds")

    topics = []
    for base, title, boss_text, operations_text in zip(
        BASE_TOPICS, TOPIC_TITLES, BOSS_TEXTS, OPERATIONS_TEXTS
    ):
        topic = copy.deepcopy(base)
        topic["title"] = title
        topic["boss"] = boss_text
        topic["operations"] = operations_text
        topics.append(topic)

    records = []
    index = 0
    for round_no, topic in enumerate(topics, start=1):
        for turn_no, role_key in enumerate(ROLE_ORDER, start=1):
            index += 1
            speaker = SPEAKERS[role_key]
            text = topic[role_key].strip()
            records.append(
                {
                    "segment_id": f"role_meeting_v2_{index:03d}",
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
        "# 制造业月度管理会：四语岗位化会议稿 v2",
        "",
        "- 目标：约 25 分钟的角色化制造业月度管理会议",
        f"- 结构：{ROUNDS} 轮 × 4 人 × 每段目标 {TARGET_SEC_PER_TURN} 秒；固定顺序为老板、生产、销售、财务",
        "- 老板：常凯申，厂长 / 总经理，普通话；口播直接围绕数据、风险和决策，不做岗位自我介绍",
        "- 生产与设备经理：曼波，四川方言；音色 ID：`clone_sichuan_dialect`",
        "- 四川方言台词采用四川话口语音译写法，保留“啷个、啥子、莫得、整、摆、安逸、恼火”等表达；技术指标和数字保持清楚",
        "- 销售与客户平台主管：特朗普，英语",
        "- 财务、采购与合规负责人：魏德尔，德语",
        "- 任务安排：老板分别给曼波、特朗普、魏德尔以及自己安排任务；JSONL 的 `status_hint` 仅作语义评估参考",
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

    counts = {
        key: sum(item["speaker_id"] == SPEAKERS[key]["speaker_id"] for item in records)
        for key in ROLE_ORDER
    }
    task_topics = sum(1 for topic in topics if topic.get("task_owner"))
    rough_total = round(sum(item["rough_duration_sec"] for item in records) / 60, 1)
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_MD}")
    print(f"segments={len(records)}, per_role={counts}, task_topics={task_topics}, rough_minutes={rough_total}")


if __name__ == "__main__":
    generate()
