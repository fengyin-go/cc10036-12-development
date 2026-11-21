# 多维分析查询与指标平台

面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
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
| 指标定义 | `metric_def` | 指标 | 指标编码、指标名称、业务域 |
| 多维立方 | `cube_builder` | OLAP立方 | 立方名称、事实表、维度列表 |
| 切片查询 | `slice_query` | 查询模板 | 模板名称、数据立方、查询维值 |
| 分群圈选 | `segment_rule` | 分群规则 | 分群名称、人群类型、筛选条件 |
| 看板布局 | `dashboard_layout` | 看板配置 | 看板名称、所属业务域、图表布局 |
| 即席导出 | `adhoc_export` | 导出任务 | 任务名称、查询SQL、导出格式 |
| 报表订阅 | `report_subscription` | 订阅配置 | 订阅名称、报表对象、推送周期 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则名称、监控指标、检测算法 |
| 下钻路径 | `drill_path` | 下钻配置 | 路径名称、起始维度、下钻层级 |
| 语义层 | `semantic_layer` | 语义定义 | 语义名称、物理表、字段映射 |
| 查询加速 | `query_accelerator` | 加速配置 | 配置名称、目标查询、加速方式 |
| 归因模型 | `attribution_model` | 归因配置 | 模型名称、归因对象、归因窗口 |
| 同期群分析 | `cohort_analysis` | 群组配置 | 群组名称、初始事件、回访事件 |
| 数据血缘 | `data_lineage` | 血缘节点 | 节点名称、节点类型、上游列表 |
| 查询调度 | `query_scheduler` | 调度计划 | 计划名称、查询语句、执行周期 |
| 结果缓存 | `result_cache` | 缓存条目 | 缓存键、查询描述、结果行数 |
| 数据范围 | `olap_scope` | 范围规则 | 规则名称、数据范围、授权对象 |
| 实验分析 | `ab_analysis` | 实验配置 | 实验名称、对照组、实验组 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 切片查询的跨期区域收入对照

切片查询页（`/slice_query`）底部多了一块“跨期区域收入对照”，把相邻两期的区域收入
放到同一口径下比较。核心约束是**只汇总当期实际有授权的区域**，因此两期的参与分区
可以不同，报表必须把这件事讲清楚。

- 授权来源：`app/services/olap_scope.py` 的 `resolve_period_grants` 按规则的
  `生效时间/失效时间` 做期间切片，因此已冻结的历史规则（如 2026Q3 的季度授权）
  仍然计入当时可读区域；规则当前状态只影响“还能不能继续授权”。
- 区域口径：`地区 in (...)` 与 `城市 in (...)` 都会折算到区域；无区域条件且
  `可见字段=全部` 的规则视为全区域授权。
- 可比口径：两期都能读的区域才进入 `comparable_revenue`；本期新增归属的区域
  （示例中的西南只读）进入 `new_region_revenue` 单列，不混入收入变动。
- 参与分区：每期返回 `participating_regions / excluded_regions`，并为每个区域
  带上 `access_level / reason / rule_ids / rule_names`，报表直接标注“为什么纳入”。
- 只读：只读区域只给区域汇总，不展开城市明细（`region_detail` 不含该区域）。
- 留档与共享：生成结果按 `comparison_id` 深拷贝留档，`GET .../{id}` 回查的是
  生成当时的授权快照，不受后续授权变更影响；`.../{id}/grants` 列出该分析员的
  全部历史授权区间。`POST .../{id}/share` 按查看者的**实时**授权逐区域裁剪，
  未授权区域连区域名都不下发，只读区域同样不展开明细。

接口（`backend/app/routers/resource.py`，注册在 `/{module}/{entry_id}` 之前）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/slice_query/period-compare` | 生成对照，入参 `principal/period_a/period_b/template_id` |
| GET | `/api/slice_query/period-compare` | 列出留档，可按 `principal` 过滤 |
| GET | `/api/slice_query/period-compare/{id}` | 回查历史快照 |
| GET | `/api/slice_query/period-compare/{id}/grants` | 历史授权区间 |
| POST | `/api/slice_query/period-compare/{id}/share` | 生成受限共享视图 |

区域收入示例数据在 `backend/app/revenue_facts.py`，测试在
`backend/tests/test_period_compare.py`（`cd backend && .venv/bin/python -m unittest discover -s tests`）。

领域：数据工程
子方向：数据分析与数据应用
