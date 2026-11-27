# 教学诊改协同台

教师、教研室主任和教务处在课堂观摩后填写教学反馈、制定改进方案、跟踪实施进度并在学期末完成复盘归档。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              21个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 课堂观摩 | `observation` | 观摩记录 | 教师姓名、课程名称、授课日期 |
| 教学反馈 | `feedback` | 反馈单 | 反馈对象、反馈要点、改进建议 |
| 改进方案 | `improve_plan` | 改进方案 | 关联反馈、改进目标、改进措施 |
| 实施进展 | `progress_log` | 进展记录 | 关联方案、当前阶段、完成情况 |
| 同行评议 | `peer_review` | 评议表 | 被评教师、评议维度、得分 |
| 教学档案 | `teach_record` | 档案条目 | 教师姓名、档案类型、内容摘要 |
| 学期目标 | `semester_goal` | 学期目标 | 目标描述、量化指标、责任教师 |
| 复盘会议 | `review_meeting` | 会议纪要 | 会议主题、参会人员、召开日期 |
| 诊改任务 | `improve_task` | 诊改任务 | 任务描述、承接方案、执行人员 |
| 外部评估 | `external_eval` | 评估报告 | 评估机构、评估指标、评估结论 |
| 课程组编排 | `course_group` | 课程组 | 组名、覆盖课程、组长教师 |
| 大纲修订 | `syllabus_rev` | 修订稿 | 课程名称、修订章节、原内容 |
| 教学成果申报 | `teach_award` | 成果申报 | 成果名称、完成团队、成果概要 |
| 集体备课 | `lesson_prep` | 备课记录 | 备课章节、主备教师、参与教师 |
| 师徒结对 | `mentor_pair` | 结对记录 | 指导教师、青年教师、结对目标 |
| 试卷分析 | `exam_analysis` | 分析记录 | 考试名称、任课教师、参考人数 |
| 排课编排 | `class_schedule` | 排课单 | 课程名称、授课教师、上课时间 |
| 教学资源申请 | `resource_apply` | 资源申请 | 申请资源、用途说明、使用时间 |
| 质量简报 | `quality_report` | 质量简报 | 覆盖学期、教学质量概况、突出问题 |
| 教材选用 | `textbook_select` | 选用单 | 教材名称、主编、出版社 |
| 教材到货 | `textbook_arrival` | 到货记录 | 订单编号、回单编号、到货批次 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 教材分批到货与大纲采用

- `textbook_arrival` 提供到货文件上传与回单导入回执核对：
  - `POST /api/textbook_arrival/upload`：multipart 上传 CSV（表头见模块字段），
    返回导入回执（新增/重复/跳过行数、回单状态、已核对数量）；
  - `POST /api/textbook_arrival/import`：粘贴多行 JSON 导入，语义与上传一致；
  - `GET /api/textbook_arrival/receipts`：按订单 + 回单聚合的回执核对清单。
- 幂等键为「订单编号 + 回单编号 + 到货批次 + 教材名称」。同一回单重复导入
  只返回回执、不重复计数；到货必须匹配订单所属课程和教材，其他课程的同名书
  不能挂到本订单（如 H-31 只计数据结构课程的《数据结构（第2版）》）。
- 回单核对后 `已核对` 数量计入订单；发现缺货时执行“更正缺货”（可附缺货证明说明），
  该批次不再计入已核对数量，并同步刷新同课程待审批大纲的采用资格。
- 列表 `textbook_arrival` 同时就是缺货证明的录入入口，可按订单/批次筛选。
- 大纲修订（`syllabus_rev`）在审批入口按到货情况判定采用资格：
  - `GET /api/syllabus_rev/{id}/adoption` 查询当前资格与数量明细；
  - “办理采用”仅在已核对数量满足需求时成功；
  - 不足时须“确认替换教材”（补填替换教材与替换依据），再提交审批；
  - 每次审批通过会把当时使用的教材与回单编号写入 `审批时教材` / `审批时回单编号`
    及 `审批快照`，后续到货更正不会改写已批准版本。

领域：应用开发
子方向：web前端/全栈开发
