import asyncio
import hashlib
import logging
import os
import time
from typing import List, Dict, Set, Optional, Any
from fastapi import WebSocket

from gpt_researcher import GPTResearcher

logger = logging.getLogger(__name__)


class DetailedReport:
    def __init__(
        self,
        query: str,
        report_type: str,
        report_source: str,
        source_urls: List[str] = [],
        document_urls: List[str] = [],
        query_domains: List[str] = [],
        config_path: str = None,
        tone: Any = "",
        websocket: WebSocket = None,
        subtopics: List[Dict] = [],
        headers: Optional[Dict] = None,
        complement_source_urls: bool = False,
        mcp_configs=None,
        mcp_strategy=None,
        max_search_results=None,
        doc_path="",
    ):
        logger.info("▶ DetailedReport.__init__ — 初始化详细报告生成器 | 入参: query=%s, report_type=%s", query, report_type)
        self.query = query
        self.report_type = report_type
        self.report_source = report_source
        self.source_urls = source_urls
        self.document_urls = document_urls
        self.query_domains = query_domains
        self.config_path = config_path
        self.tone = tone
        self.websocket = websocket
        self.subtopics = subtopics
        self.headers = headers or {}
        self.complement_source_urls = complement_source_urls
        self.max_search_results = max_search_results
        
        # Generate a unique research ID for this report
        self.research_id = self._generate_research_id(query)
        
        # Initialize researcher with optional MCP parameters
        gpt_researcher_params = {
            "query": self.query,
            "query_domains": self.query_domains,
            "report_type": "research_report",
            "report_source": self.report_source,
            "source_urls": self.source_urls,
            "document_urls": self.document_urls,
            "config_path": self.config_path,
            "tone": self.tone,
            "websocket": self.websocket,
            "headers": self.headers,
            "complement_source_urls": self.complement_source_urls,
        }

        # Add MCP parameters if provided
        if mcp_configs is not None:
            gpt_researcher_params["mcp_configs"] = mcp_configs
        if mcp_strategy is not None:
            gpt_researcher_params["mcp_strategy"] = mcp_strategy

        self.gpt_researcher = GPTResearcher(**gpt_researcher_params)

        # Override max_search_results_per_query if provided by user
        if max_search_results is not None:
            self.gpt_researcher.cfg.max_search_results_per_query = int(max_search_results)
        # Override doc_path if provided by frontend (local/hybrid mode)
        if doc_path:
            # Strip invisible Unicode bidirectional control characters
            # that Windows Explorer sometimes prepends when copying paths
            doc_path = doc_path.strip().strip("\u200e\u200f\u202a\u202b\u202c\u202d\u202e")
            os.makedirs(doc_path, exist_ok=True)
            self.gpt_researcher.cfg.doc_path = doc_path
        self.existing_headers: List[Dict] = []
        self.global_context: List[str] = []
        self.global_written_sections: List[str] = []
        self.global_urls: Set[str] = set(
            self.source_urls) if self.source_urls else set()

    def _generate_research_id(self, query: str) -> str:
        """Generate a unique research ID from query and timestamp."""
        logger.info("▶ DetailedReport._generate_research_id — 生成唯一研究ID | 入参: query=%s", query)
        timestamp = str(int(time.time()))
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        return f"detailed_{timestamp}_{query_hash}"

    async def run(self) -> str:
        logger.info("▶ DetailedReport.run — 执行详细报告的研究和生成流程")
        await self._initial_research()
        subtopics = await self._get_all_subtopics()
        report_introduction = await self.gpt_researcher.write_introduction()
        _, report_body = await self._generate_subtopic_reports(subtopics)
        self.gpt_researcher.visited_urls.update(self.global_urls)
        report = await self._construct_detailed_report(report_introduction, report_body)
        return report

    async def _initial_research(self) -> None:
        logger.info("▶ DetailedReport._initial_research — 执行初始研究")
        await self.gpt_researcher.conduct_research()
        self.global_context = self.gpt_researcher.context
        self.global_urls = self.gpt_researcher.visited_urls

    async def _get_all_subtopics(self) -> List[Dict]:
        logger.info("▶ DetailedReport._get_all_subtopics — 获取所有子主题")
        subtopics_data = await self.gpt_researcher.get_subtopics()

        all_subtopics = []
        if subtopics_data and subtopics_data.subtopics:
            for subtopic in subtopics_data.subtopics:
                all_subtopics.append({"task": subtopic.task})
        else:
            print(f"Unexpected subtopics data format: {subtopics_data}")

        return all_subtopics

    async def _generate_subtopic_reports(self, subtopics: List[Dict]) -> tuple:
        logger.info("▶ DetailedReport._generate_subtopic_reports — 生成子主题报告 | 入参: subtopics_count=%d", len(subtopics))
        subtopic_reports = []
        subtopics_report_body = ""

        for subtopic in subtopics:
            result = await self._get_subtopic_report(subtopic)
            if result["report"]:
                subtopic_reports.append(result)
                subtopics_report_body += f"\n\n\n{result['report']}"

        return subtopic_reports, subtopics_report_body

    def _hashable_context(self, input_context: List[str] | List[dict]):
        logger.info("▶ DetailedReport._hashable_context — 将上下文转换为可哈希的字符串列表 | 入参: item_count=%d", len(input_context))
        # Convert context to strings to ensure hashability (handle both strings and dicts from MCP)
        context_items = []
        
        for item in input_context:
            if isinstance(item, dict):
                # Convert dict context to string format
                title = item.get("title", "No title")
                content = item.get("body", item.get("content", ""))
                context_str = f"Title: {title}\nContent: {content}"
                context_items.append(context_str)
            else:
                context_items.append(str(item))
        
        return context_items

    async def _get_subtopic_report(self, subtopic: Dict) -> Dict[str, str]:
        current_subtopic_task = subtopic.get("task")
        logger.info("▶ DetailedReport._get_subtopic_report — 获取单个子主题报告 | 入参: subtopic_task=%s", current_subtopic_task)
        subtopic_assistant = GPTResearcher(
            query=current_subtopic_task,
            query_domains=self.query_domains,
            report_type="subtopic_report",
            report_source=self.report_source,
            websocket=self.websocket,
            headers=self.headers,
            parent_query=self.query,
            subtopics=self.subtopics,
            visited_urls=self.global_urls,
            agent=self.gpt_researcher.agent,
            role=self.gpt_researcher.role,
            tone=self.tone,
            complement_source_urls=self.complement_source_urls,
            source_urls=self.source_urls,
            # Propagate MCP configuration so follow-up researchers can use MCP
            mcp_configs=self.gpt_researcher.mcp_configs,
            mcp_strategy=self.gpt_researcher.mcp_strategy
        )

        # Propagate max_search_results override to subtopic researcher
        if self.max_search_results is not None:
            subtopic_assistant.cfg.max_search_results_per_query = int(self.max_search_results)

        subtopic_assistant.context = list(set(self._hashable_context(self.global_context)))
        await subtopic_assistant.conduct_research()

        draft_section_titles = await subtopic_assistant.get_draft_section_titles(current_subtopic_task)

        if not isinstance(draft_section_titles, str):
            draft_section_titles = str(draft_section_titles)

        parse_draft_section_titles = self.gpt_researcher.extract_headers(draft_section_titles)
        parse_draft_section_titles_text = [header.get(
            "text", "") for header in parse_draft_section_titles]

        relevant_contents = await subtopic_assistant.get_similar_written_contents_by_draft_section_titles(
            current_subtopic_task, parse_draft_section_titles_text, self.global_written_sections
        )

        # Write subtopic report (images are pre-generated at the main research level)
        subtopic_report = await subtopic_assistant.write_report(
            existing_headers=self.existing_headers,
            relevant_written_contents=relevant_contents,
        )

        self.global_written_sections.extend(self.gpt_researcher.extract_sections(subtopic_report))
        self.global_context = list(set(self._hashable_context(subtopic_assistant.context)))
        self.global_urls.update(subtopic_assistant.visited_urls)

        self.existing_headers.append({
            "subtopic task": current_subtopic_task,
            "headers": self.gpt_researcher.extract_headers(subtopic_report),
        })

        return {"topic": subtopic, "report": subtopic_report}

    async def _construct_detailed_report(self, introduction: str, report_body: str) -> str:
        logger.info("▶ DetailedReport._construct_detailed_report — 构建完整详细报告")
        toc = self.gpt_researcher.table_of_contents(report_body)
        conclusion = await self.gpt_researcher.write_report_conclusion(report_body)
        conclusion_with_references = self.gpt_researcher.add_references(
            conclusion, self.gpt_researcher.visited_urls)
        report = f"{introduction}\n\n{toc}\n\n{report_body}\n\n{conclusion_with_references}"
        
        # Note: Images are now pre-generated during conduct_research() and embedded during write_report()
        return report
