# smartCodingReview

COMP9313 AI Coding Review System

Project Brief — Initial Planning Stage

1. 项目背景与目标

我计划开发一个 AI-assisted Coding Practice System，最初用于 UNSW COMP9313（Big Data Management）的 Final Exam Review，未来希望扩展到其他计算机课程。

目前项目仍处于规划阶段，尚未开始编写代码、建立仓库或确定最终技术架构。

我希望借助 Vibe Coding，特别是 OpenAI Codex，逐步开发系统。同时将其作为一个 AI Engineering 实践项目，未来可以放到公开 GitHub 仓库。

项目有两个主要目标：

短期目标： 高效准备 COMP9313 Final Exam，通过 AI 生成编程题、自己完成代码、本地自动测试来巩固知识。

长期目标： 将初期依赖 Codex 的工作流逐渐发展为独立的 AI Coding Tutor，实践 RAG、LLM API、自动化评估等 AI Engineering 技术。

项目首先服务于个人学习，暂不考虑多用户、商业部署或复杂网页界面。

2. 核心功能需求

2.1 课程资料导入与分析

系统需要能够读取我本地保存的 Lecture PDF、Tutorial 或其他课程资料。

初期希望直接利用 Codex 的文件读取和代码分析能力，不急于建立完整的文档解析或 RAG 系统。

希望 AI 能够：

扫描 Lecture PDF。

提取与编程相关的知识点。

识别重要的 API、算法和数据处理模式。

建立按 Topic 分类的知识点索引。

尽可能保留来源 Lecture 和页码。

根据资料内容判断哪些知识点适合生成编程练习。

不要求所有知识点都生成题目；重点是实际编程和代码理解能力。

2.2 自动生成编程题

项目不需要传统的选择题系统，因为一般概念题和 MCQ 可以直接使用网页版 ChatGPT 完成。

重点是以下题型：

Coding Implementation：从头实现函数。

Code Completion：补全未完成的代码。

Debugging：找到并修复代码中的错误。

Code Tracing / Output Prediction：分析程序流程和输出。

Algorithm Implementation：根据课程内容实现具体算法。

以 COMP9313 为例，可能涉及：

PySpark RDD（map、flatMap、reduceByKey、aggregateByKey 等）。

PySpark DataFrame 和 Spark SQL。

MapReduce / MRJob。

In-Mapper Combining、Secondary Sort、Order Inversion。

课程实际涵盖的其他数据处理算法。

题目应能够设置 Topic、Difficulty 和 Exercise Type。

Codex 初期负责生成：

question.md：题目说明、输入输出和约束。

student.py：函数签名和待完成的骨架。

reference.py：独立参考实现。

test_student.py：自动化测试。

metadata.json：知识点、难度和来源等结构化信息。

参考答案需要与学生作答文件分离，避免影响自主学习。

2.3 本地编程与自动测试

我希望主要在 VS Code 或 Jupyter Notebook 中编写答案，而不是构建在线代码编辑器。

基本流程：

AI 生成题目和函数骨架。

我自己编写代码。

本地运行 PySpark / MRJob / Python。

使用 pytest 检查输出和边界情况。

Codex 分析测试失败的原因。

记录错误和对应知识点。

自动测试结果应该比 LLM 的主观评分具有更高优先级。

希望测试能够覆盖正常输入、边界输入、空数据、重复记录、输入顺序变化，以及题目指定的 API 使用约束。

对于分布式处理问题，还需要合理处理无序输出、分区行为等特点。

模型生成的参考实现与测试也需要校验，不能认为生成成功就代表题目正确。

2.4 学习反馈与错题系统

初期可以只使用 Markdown 或 JSON 保存练习记录。

未来希望加入：

各 Topic 的完成情况。

测试通过率和错误类型。

重复出现的编程问题。

根据错误生成针对性练习。

分级提示，避免直接展示完整答案。

Exam Mode：限制提示和 AI 辅助，检验独立编程能力。

暂不需要复杂的数据可视化 Dashboard。

3. 初步开发路线

希望采用渐进式开发，而非一次性完成所有功能。

Phase 1 — Codex-first Prototype

主要依赖 Codex 直接读取本地课程资料。

任务：

初始化 Python 项目。

使用 AGENTS.md 定义 Codex 行为。

通过 Codex 扫描 Lecture。

建立 Topic Map。

生成少量可运行的编程练习。

在本地手动执行测试。

此时不需要 LangChain、RAG、Vector Database 或前端。

阶段目标：跑通「课程资料 → 编程题 → 自己解题 → 测试 → 反馈」的完整流程。

Phase 2 — Automated Testing

使系统的测试能力更稳定。

重点考虑：

pytest。

PySpark local mode。

MRJob local runner。

独立参考实现。

自动测试生成与验证。

错误分类。

测试执行的超时和隔离机制。

Phase 3 — Question Bank & CLI

逐渐将重复的 Codex 操作封装为 Python 函数。

可能提供以下功能：

create_exercise()

validate_exercise()

run_tests()

record_attempt()

get_review_summary()

建立结构化题库和简单命令行界面。

可能使用 JSON、Pydantic 和 SQLite，但不要求最初全部实现。

Phase 4 — RAG and LangChain

当资料数量增多，或者 Codex 直接扫描不能满足需求时，再引入独立 RAG。

可能采用：

PyMuPDF：PDF Parsing。

Chunking：按章节、代码块等分割。

Embedding：语义表示。

Chroma / FAISS：向量存储。

Hybrid Retrieval：关键字与语义检索结合。

LangChain：组织检索和 LLM 调用。

希望生成的问题能够引用具体知识点和课程来源。

目前尚未决定 Embedding Model、Vector Database 或 LLM Provider。

Phase 5 — Advanced AI Features（尚未确定）

潜在发展方向包括：

A. Adaptive Learning

根据过去的错误和学习进度自动安排练习。

B. Agentic Coding Tutor

利用 LangGraph 或其他编排方式，把题目生成、测试验证、批改和提示拆成可控制的工作流。

C. AI Evaluation

评估不同模型的出题质量、参考答案准确率、测试覆盖度、批改一致性和 Token 成本。

这些都只是潜在方向，暂不希望过早确定架构。

4. 初步技术选型

领域

当前考虑

主要语言

Python 3.11

开发环境

VS Code / Jupyter

AI Coding Agent

Codex

课程资料

本地 PDF

本地计算

PySpark、MRJob

自动测试

pytest

初期数据存储

Markdown / JSON

后期数据存储

SQLite

后期 LLM

OpenAI API / GLM / 其他模型

PDF Processing

PyMuPDF

后期 RAG

LangChain + Chroma / FAISS

Agent Orchestration

LangGraph（可选）

测试隔离

Docker（根据需要）

版本控制

Git + GitHub

这些只是候选方案，希望在架构讨论时进行取舍。

5. GitHub、API Key 和课程资料安全

我计划未来将项目以 Public Repository 的形式放在 GitHub。

但系统涉及个人 API Key、本地 Lecture PDF，以及不应该公开的课程材料。

因此必须在开发初期考虑安全。

5.1 API Key

不在源码中硬编码 Key。

使用环境变量或本地 .env。

.env 不提交到 Git。

仓库只包含 .env.example。

不在日志、测试输出或错误报告中泄露凭据。

使用单独的项目 API Key，并考虑成本与用量限制。

5.2 Course Materials

Lecture PDF 不上传 GitHub。

优先放在 Git 仓库之外。

本地路径通过环境变量配置，例如 LECTURE_DIR。

不在源码、README 或公开 JSON 中保存真实绝对路径。

RAG Chunk、Embedding、Vector Database、学习记录可能含有私人资料，也需要排除。

公开示例和测试应尽可能使用自行编写的合成数据，而非课程原文。

希望公开仓库中只包含代码、公共配置模板、文档及安全的测试示例。

5.3 Codex Security

希望 AGENTS.md 包含明确规则：

不自动提交 Lecture、PDF 提取内容或 API Key。

不随意复制课程内容到公开目录。

不自动执行 Git Push。

提交前检查 Git 状态和改动。

不允许模型随意覆盖学生答案。

未经适当隔离，不执行不可信代码。

还希望加入 Git Secret Scanning / Gitleaks 或类似措施，减少意外泄露。

必须注意 .gitignore 无法清除已经进入 Git 历史的文件。

6. 目前的架构偏好

有几个明确倾向：

第一，尽量减少 Overengineering。

初期不要为了练习某项技术就强行引入它。例如不需要马上做 React、FastAPI、LangChain 或 LangGraph。

第二，以实际学习效果为优先。

COMP9313 Final Review 是短期最重要的目标，项目开发不能反过来占据过多复习时间。

第三，先验证流程，再自动化。

先让 Codex 人工参与整个工作流，观察哪些步骤反复出现，再考虑封装这些步骤。

第四，保留可扩展性，但不需要提前完成复杂架构。

例如早期使用标准化 Metadata，使未来升级到独立出题服务或 RAG 时能够复用已有题目。

第五，AI 不应该代替我完成复习。

AI 可以生成题目、测试和分级提示，但真正需要我独立编写代码。

7. 希望在下一轮讨论中解决的问题

目前不需要立即生成整个项目。我希望先与你进行技术设计讨论，重点包括：

Codex-first 是否是合理的 MVP 路线？ 哪些工作应该直接交给 Codex，哪些应该尽早变成固定 Python 模块？

项目目录怎样设计最合适？ 既要简单，又要兼容未来的 RAG、Question Bank 和 CLI。

怎样验证 AI 生成的编程题与测试是正确的？ 尤其是 PySpark、MRJob 和分布式数据处理题目。

如何设计一个安全的代码执行流程？ 本地执行、独立进程、Docker 等方式应该如何选择？

如何隔离私有 Lecture 与公开 GitHub 仓库？ 包括 Codex、Git 历史、临时文件、测试数据和未来的 RAG 存储。

什么时候才值得引入 LangChain、LangGraph 和独立 LLM API？ 有没有更轻量的替代方案？

有哪些可能忽略的架构风险？ 例如 LLM 测试不可靠、PDF 扫描质量、模型上下文、运行成本和环境兼容性。

8. 期望的回答方式

请先评估以上方案的合理性，不要直接编写完整系统。

希望你：

指出不合理、重复或过度设计的部分。

推荐一个尽可能精简的 MVP。

给出适合初学阶段的模块划分。

区分必须现在决定与可以以后再决定的技术。

重点考虑 AI Coding Workflow、可测试性、隐私和 GitHub 安全。

提出适合以后扩展的架构，而不是立即实施所有功能。

最终目标是形成一份清晰、可执行、可以逐步迭代的开发计划。
