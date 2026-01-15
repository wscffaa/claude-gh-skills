---
name: omo-ofr-agents
description: |
  BasicOFR 老电影修复专用的 OMO 多代理编排套件。提供 7 个领域专家代理（编排/架构/研究/代码定位/可视化Debug/写作/多模态）+ 1 个远程运维代理（可选），用于把“论文+代码”稳定转化为 BasicOFR 可训练的创新实现，并能迭代到实验验证与论文撰写。
---

# OMO-OFR Agents（BasicOFR 老电影修复多代理套件）

面向 OFR 工作流（Phase 0-5），把通用多代理分工“定制”为老电影修复 + BasicOFR 框架的专用专家。

## 代理一览（@agent）

| 代理 | 角色 | 何时调用 |
|------|------|----------|
| **@ofr-sisyphus** | 总编排/守门人 | 端到端推进（ofr-idea-source → ofr-idea-analyze → ofr-idea-integrate → debug/experiment），管理状态与门控 |
| **@ofr-oracle** | 架构顾问 | 选 Baseline（RTN/RRTN/MambaOFR）、确定集成点/消融矩阵、风险评估 |
| **@ofr-librarian** | 文献研究员 | 论文贡献梳理、同类方法对比、是否适合老电影退化与 BasicOFR |
| **@ofr-explore** | 代码定位专家 | 快速定位外部代码与 BasicOFR 对齐点（registry/forward/IO） |
| **@ofr-visualization-engineer** | 可视化/Debug 专家 | `visualiza_feature` / mask / feature 能量图设计、异常分类与修复建议 |
| **@ofr-paper-writer** | SCI 写作专家 | Phase 0.5 故事线、贡献表达、实验表格与图注，面向审稿人 |
| **@ofr-multimodal-looker** | 多模态分析师 | 读架构图/截图/可视化结果，提炼关键现象与定位线索 |
| **@ofr-remote-operator** | 远程运维专家（SSH/git/tmux） | Phase 3.5/4/长训：在服务器跑训练/Debug，并把关键产物通过 gitee 回传 |

## 与 OFR 工作流的关系（推荐分工）

| 阶段 | Skill | 主责代理 | 典型辅助 |
|------|-------|----------|----------|
| Phase 0-2 | ofr-idea-analyze | @ofr-librarian / @ofr-explore | @ofr-naming-architect / @ofr-oracle |
| Phase 3-5 | ofr-idea-integrate | @ofr-oracle / @ofr-sisyphus | @ofr-error-advisor |
| Phase 6 | ofr-exp-train (架构 Debug) | @ofr-visualization-engineer | @ofr-multimodal-looker |
| Phase 7+ | ofr-exp-test / ofr-exp-train | @ofr-sisyphus | @ofr-remote-operator |

## 典型使用方式

### 端到端（建议直接找编排者）

```
@ofr-sisyphus
我给你 arXiv:2501.04486 + 对应 GitHub 链接。请按项目规范：
1) 先用 ofr-idea-source 落盘到 Ideas/{ProjectName}/Latex+Codes
2) 再跑 ofr-idea-analyze（Phase 0-2），输出到 specs/4xx-proj-*（00/01/02 + naming/）
3) 再跑 ofr-idea-integrate（Phase 3-5），最后落地到 basicofr/archs/ideas/{project}/ 与 options/train/{project}/
4) 保持 anything2ofr 作为全流程兼容入口：需要一键跑全流程时直接用 anything2ofr 即可
```

### 针对性咨询（按需调用）

```
@ofr-oracle 这个创新点更适合挂在 RTN 的哪个位置？需要哪些消融？
@ofr-explore 帮我找到 BasicOFR 里和“mask 引导融合”最接近的实现位置
@ofr-visualization-engineer 设计一套能证明创新点生效的可视化输出（mask/feature）
@ofr-paper-writer 把最终创新点组织成 SCI 故事线（动机→方法→实验→贡献）
```

## 使用原则（强约束）

1. **单写入者**：并行代理只输出“定位/建议/研究”，最终改代码与改配置由主执行者统一落地，避免状态分叉。
2. **BasicOFR 规范优先**：路径、命名、`visualiza_feature`、消融矩阵、debug 门控以 OFR 工作流规范/Spec 为准。
3. **先可验证后扩展**：先做 debug/短训闭环，再上长训与远程实验。

## 快速入口

- Agent 文件索引：`.claude/skills/omo-ofr-agents/AGENTS.md`
- Agent 定义目录：`.claude/agents/`
