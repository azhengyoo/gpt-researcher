现在我有完整的代码路径了。以下是 **一次研究请求从进来到结束的完整代码路径**：

---

# 🔍 完整请求追踪：WebSocket 研究请求

## 第一阶段：请求接收与路由

```
main.py:40  main()
  └─> uvicorn.run(app)  # FastAPI 启动
      
main.py:338  @app.websocket("/ws")  # WebSocket 连接入口
  └─> manager.connect(websocket)  # websocket_manager.py:50
  └─> handle_websocket_communication(websocket, manager)  # server_utils.py:347
```

### 📡 server_utils.py:347 `handle_websocket_communication()`

```
:372  while True:
:374    data = await websocket.receive_text()    # 接收前端消息
:403    if data.startswith("start"):             # 检测 "start" 命令
:406      await websocket.send_json({"type":"ack"})  # 立即回复 ACK
:410      run_long_running_task(                  # 启动异步任务
:411        handle_start_command(websocket, data, manager)
```

### 📡 server_utils.py:129 `handle_start_command()`

```
:130  json_data = json.loads(data[6:])            # 解析 JSON
:131  extract_command_data(json_data)             # :452 提取 task, report_type, tone 等
:175  report = await manager.start_streaming(     # WebSocketManager.start_streaming
         task, report_type, report_source, ..., websocket, ...
       )
```

---

## 第二阶段：报告类型路由

### 📡 websocket_manager.py:98 `start_streaming()`

```
:100  tone = Tone[tone]
:105  report = await run_agent(                   # 调用 run_agent
         task, report_type, report_source, ..., websocket, ...
       )
```

### 📡 websocket_manager.py:113 `run_agent()`

```
:119  logs = CustomLogsHandler(websocket, task)   # 创建日志处理器
:148  if report_type == "detailed_report":
:149    researcher = DetailedReport(...)           # 详细报告
:165    report = await researcher.run()
:167  else:
:168    researcher = BasicReport(...)              # 基础报告（默认路径）
:184    report = await researcher.run()
```

---

## 第三阶段：BasicReport/DetailedReport

### 📄 basic_report.py:81 `BasicReport.run()`（最常用路径）

```
:82  await self.gpt_researcher.conduct_research()   # ★ 核心研究
:83  report = await self.gpt_researcher.write_report()  # ★ 生成报告
:84  return report
```

### 📄 detailed_report.py:93 `DetailedReport.run()`（详细报告路径）

```
:94   await self._initial_research()                 # 初次研究
:95   subtopics = await self._get_all_subtopics()    # 获取子主题
:96   intro = await self.gpt_researcher.write_introduction()  # 写引言
:97   _, body = await self._generate_subtopic_reports(subtopics)  # 每个子主题递归研究
:99   report = await self._construct_detailed_report(intro, body)  # 组装报告
```

---

## 第四阶段：核心研究 `conduct_research()`

### 🧠 agent.py:336 `GPTResearcher.conduct_research()`

```
:360  if not (self.agent and self.role):
:363    self.agent, self.role = await choose_agent(...)  # ★ LLM 自动选择 Agent 角色
       └─> actions/agent_creator.py:18 choose_agent()
           └─> :45 create_chat_completion()  # 调用 LLM 判断最佳 Agent
           └─> :58 json.loads(response) → 返回 agent_name + agent_role_prompt

:385  self.context = await self.research_conductor.conduct_research()  # ★ 执行研究
```

### 🔬 skills/researcher.py:98 `ResearchConductor.conduct_research()`

```
:130  if not (agent and role):
:131    self.researcher.agent, self.researcher.role = await choose_agent(...)

:160  elif report_source == ReportSource.Web.value:  # ★ 默认 Web 搜索路径
:162    research_data = await self._get_context_by_web_search(query, [], query_domains)
```

### 🔬 skills/researcher.py:568 `_get_context_by_web_search()`

```
:585  mcp_strategy = self._get_mcp_strategy()        # 获取 MCP 策略 (fast/deep/disabled)
:589  async with self._mcp_cache_lock:
:590    if mcp_retrievers and cache is None:
:601      if mcp_strategy == "fast":
:613        mcp_context = await self._execute_mcp_research_for_qu(...)  # MCP 搜索
:614        self._mcp_results_cache = mcp_context

:635  sub_queries = await self.plan_research(query)  # ★ LLM 规划子查询
       └─> researcher.py:51 plan_research()
           └─> :65 get_search_results() → 先搜索一轮
           └─> :84 plan_research_outline() → LLM 生成研究大纲和子查询

:654  context = await asyncio.gather(                # ★ 并行处理每个子查询
         *[self._process_sub_query(sub_query, ...) for sub_query in sub_queries]
       )
```

### 🔬 skills/researcher.py:782 `_process_sub_query()`（每个子查询的处理）

```
:800  mcp_retrievers = [...]
:803  if tavily_mcp_redundant: mcp_retrievers = []  # 去重

:826  if mcp_strategy == "fast" and cache exists:
:831    mcp_context = self._mcp_results_cache.copy()  # 复用缓存

:869  scraped_data = await self._scrape_data_by_urls(sub_query)  # ★ 搜索+抓取
       └─> researcher.py:1147 _scrape_data_by_urls()
           └─> :1162 new_search_urls, prefetched = await self._search_relevant_source_urls(...)
           │   └─> researcher.py:1098 _search_relevant_source_urls()
           │       └─> :1129 asyncio.gather(  # 并行调用所有检索器
           │             *[_search_with_retriever(rc) for rc in self.researcher.retrievers]
           │           )
           │       └─> :1104 _search_with_retriever()
           │           └─> :1111 retriever = retriever_class(query, query_domains=...)
           │           └─> :1112 search_results = await asyncio.to_thread(
           │                 retriever.search, max_results=...
           │               )
           │           └─> 返回 URL 列表 + 预抓取内容
           └─> :1174 scraped_content = await self.researcher.scraper_manager.browse_urls(...)
               └─> skills/browser.py:37 browse_urls()
                   └─> :55 scraped_content, images = await scrape_urls(urls, cfg, worker_pool)
                   └─> :58 self.researcher.add_research_sources(scraped_content)
                   └─> :59 self.researcher.add_research_images(new_images)

:874  web_context = await self.researcher.context_manager.get_similar_content_by_query(
         sub_query, scraped_data
       )  # ★ 用嵌入向量找最相关的内容块

:878  combined_context = self._combine_mcp_and_web_context(...)  # 合并 MCP + Web 上下文

:914  return combined_context
```

---

## 第五阶段：结果整理（回到 conduct_research）

### 🧠 agent.py:336 `conduct_research()`（继续）

```
:250  if self.cfg.curate_sources:
:253    curated = await self.researcher.source_curator.curate_sources(...)
       └─> skills/curator.py:33 curate_sources()
           └─> LLM 评估来源可信度，排序

:399  # ★ 高清图片搜索
:400  if image_sources:
:413    image_queries = await _extract_image_search_queries(...)  # agent.py:1032
         └─> LLM 从研究上下文中提取图片搜索关键词
:431    batch_results = await asyncio.gather(
         *[search_quality_images(q, cfg, ...) for q in image_queries]
       )  # 并行搜索 Pexels/Unsplash/Tavily
:449    relevant_images = filter_relevant_images(...)  # agent.py:449 过滤相关图片
:471    self.research_images = search_urls + self.research_images  # 前置高清图

:491  # ★ AI 图片生成（可选）
:495  self.available_images = await self.image_generator.plan_and_generate_images(...)

:505  # 否则使用网页抓取图片
:505  self.available_images = self._prepare_web_images_for_report()
       └─> agent.py:785 _prepare_web_images_for_report()
           └─> 过滤 icon/svg/占位图，去重，取前 10 张，为每张配 section_hint
```

---

## 第六阶段：报告生成

### 🧠 agent.py:564 `GPTResearcher.write_report()`

```
:593  report = await self.report_generator.write_report(...)
       └─> skills/writer.py:49 write_report()
           └─> actions/markdown_processing.py → generate_report()
           └─> LLM 根据上下文 + 图片生成 Markdown 报告
```

---

## 第七阶段：文件输出（回到 WebSocket 层）

### 📡 server_utils.py:129 `handle_start_command()`（继续）

```
:191  report = str(report)
:193  await websocket.send_json({"type":"report_complete","output":report})  # 推送报告
:200  file_paths = await generate_report_files(report, sanitized_filename)
       └─> server_utils.py:281 generate_report_files()
           └─> :282 write_md_to_pdf(report, filename)    # → PDF
           └─> :283 write_md_to_word(report, filename)   # → DOCX
           └─> :284 write_text_to_md(report, filename)   # → Markdown
:203  await send_file_paths(websocket, file_paths)        # 推送文件路径
```

---

## 📊 完整调用链一览

```
前端 WebSocket 消息 "start {...}"
│
├─ main.py:338                    @app.websocket("/ws")
├─ main.py:342                    handle_websocket_communication()
│   └─ server_utils.py:347        while True: receive → "start"
│       └─ server_utils.py:129    handle_start_command()
│           └─ websocket_manager.py:98   start_streaming()
│               └─ websocket_manager.py:113  run_agent()
│                   ├─ BasicReport(…).run()    basic_report.py:81
│                   │   ├─ GPTResearcher.conduct_research()  agent.py:336
│                   │   │   ├─ choose_agent()                agent_creator.py:18
│                   │   │   ├─ ResearchConductor.conduct_research()  researcher.py:98
│                   │   │   │   ├─ _get_context_by_web_search()     researcher.py:568
│                   │   │   │   │   ├─ plan_research()              researcher.py:51
│                   │   │   │   │   │   ├─ get_search_results()     query_processing.py:43
│                   │   │   │   │   │   └─ plan_research_outline()  query_processing.py
│                   │   │   │   │   └─ asyncio.gather(              researcher.py:654
│                   │   │   │   │       *_process_sub_query()       researcher.py:782
│                   │   │   │   │         ├─ _scrape_data_by_urls() researcher.py:1147
│                   │   │   │   │         │   ├─ _search_relevant_source_urls()  :1098
│                   │   │   │   │         │   │   └─ retriever.search()  → Tavily/Google/...
│                   │   │   │   │         │   └─ BrowserManager.browse_urls()  browser.py:37
│                   │   │   │   │         │       └─ scrape_urls()  → Playwright/BS4
│                   │   │   │   │         └─ ContextManager.get_similar_content_by_query()
│                   │   │   │   │             └─ 嵌入向量相似度匹配
│                   │   │   │   └─ SourceCurator.curate_sources()   curator.py:33
│                   │   │   ├─ _extract_image_search_queries()      agent.py:1032
│                   │   │   ├─ search_quality_images() × N          (Pexels/Unsplash)
│                   │   │   └─ _prepare_web_images_for_report()     agent.py:785
│                   │   └─ GPTResearcher.write_report()             agent.py:564
│                   │       └─ ReportGenerator.write_report()       writer.py:49
│                   │           └─ generate_report() → LLM 生成 Markdown
│                   └─ generate_report_files()   server_utils.py:281
│                       ├─ write_md_to_pdf()
│                       ├─ write_md_to_word()
│                       └─ write_text_to_md()
```

---

## 📋 关键函数速查表

| 阶段 | 函数 | 文件 | 行号 | 作用 |
|------|------|------|------|------|
| 入口 | `websocket_endpoint` | `main.py` | 338 | WebSocket 连接 |
| 消息处理 | `handle_websocket_communication` | `server_utils.py` | 347 | 消息循环 |
| 命令解析 | `handle_start_command` | `server_utils.py` | 129 | 解析 start 命令 |
| 流式启动 | `start_streaming` | `websocket_manager.py` | 98 | 启动流式输出 |
| 代理运行 | `run_agent` | `websocket_manager.py` | 113 | 路由到 Basic/Detailed |
| 基础报告 | `BasicReport.run` | `basic_report.py` | 81 | 基础报告执行 |
| 详细报告 | `DetailedReport.run` | `detailed_report.py` | 93 | 详细报告执行 |
| 核心研究 | `GPTResearcher.conduct_research` | `agent.py` | 336 | 总控研究流程 |
| Agent选择 | `choose_agent` | `agent_creator.py` | 18 | LLM 选择 Agent 角色 |
| 研究执行 | `ResearchConductor.conduct_research` | `researcher.py` | 98 | 协调搜索和抓取 |
| 网络搜索 | `_get_context_by_web_search` | `researcher.py` | 568 | Web 搜索主逻辑 |
| 子查询规划 | `plan_research` | `researcher.py` | 51 | LLM 生成子查询 |
| 子查询处理 | `_process_sub_query` | `researcher.py` | 782 | 单子查询搜索+抓取 |
| URL搜索 | `_search_relevant_source_urls` | `researcher.py` | 1098 | 调用检索器搜索 |
| 网页抓取 | `BrowserManager.browse_urls` | `browser.py` | 37 | 抓取网页内容 |
| 上下文压缩 | `get_similar_content_by_query` | `context_manager.py` | 37 | 嵌入向量匹配 |
| 来源评估 | `SourceCurator.curate_sources` | `curator.py` | 33 | LLM 评估来源 |
| 图片搜索 | `_extract_image_search_queries` | `agent.py` | 1032 | LLM 提取图片关键词 |
| 图片过滤 | `_prepare_web_images_for_report` | `agent.py` | 785 | 过滤/去重/匹配图片 |
| 报告生成 | `GPTResearcher.write_report` | `agent.py` | 564 | 写报告入口 |
| 报告写作 | `ReportGenerator.write_report` | `writer.py` | 49 | 调用 LLM 生成 |
| 文件输出 | `generate_report_files` | `server_utils.py` | 281 | 生成 PDF/Word/MD |

需要我深入分析某个具体环节的代码吗？