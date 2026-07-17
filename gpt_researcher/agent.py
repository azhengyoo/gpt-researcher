"""GPT Researcher agent module.

This module provides the main GPTResearcher class that orchestrates
autonomous research and report generation using LLMs and web search.
"""

import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .actions import (
    add_references,
    choose_agent,
    extract_headers,
    extract_sections,
    get_retrievers,
    get_search_results,
    stream_output,
    table_of_contents,
)
from .config import Config
from .llm_provider import GenericLLMProvider
from .memory import Memory
from .prompts import get_prompt_family
from .skills.browser import BrowserManager
from .skills.context_manager import ContextManager
from .skills.curator import SourceCurator
from .skills.deep_research import DeepResearchSkill
from .skills.image_generator import ImageGenerator
from .skills.researcher import ResearchConductor
from .skills.writer import ReportGenerator
from .utils.enum import ReportSource, ReportType, Tone
from .utils.llm import create_chat_completion
from .scraper.utils import normalize_image_url
from .skills.image_search import merge_research_images, search_quality_images
from .vector_store import VectorStoreWrapper


class GPTResearcher:
    """Main GPT Researcher agent class.

    This class orchestrates the entire research process including
    web searching, content scraping, context management, and
    report generation using LLMs.

    Attributes:
        query: The research query or question.
        report_type: Type of report to generate.
        cfg: Configuration object.
        context: Accumulated research context.
        research_costs: Total accumulated API costs.
        step_costs: Per-step cost breakdown dictionary.
    """

    def __init__(
        self,
        query: str,
        report_type: str = ReportType.ResearchReport.value,
        report_format: str = "markdown",
        report_source: str = ReportSource.Web.value,
        tone: Tone = Tone.Objective,
        source_urls: list[str] | None = None,
        document_urls: list[str] | None = None,
        complement_source_urls: bool = False,
        query_domains: list[str] | None = None,
        documents=None,
        vector_store=None,
        vector_store_filter=None,
        config_path=None,
        websocket=None,
        agent=None,
        role=None,
        parent_query: str = "",
        subtopics: list | None = None,
        visited_urls: set | None = None,
        verbose: bool = True,
        context=None,
        headers: dict | None = None,
        max_subtopics: int = 5,
        log_handler=None,
        prompt_family: str | None = None,
        mcp_configs: list[dict] | None = None,
        mcp_max_iterations: int | None = None,
        mcp_strategy: str | None = None,
        **kwargs
    ):
        """
        Initialize a GPT Researcher instance.
        
        Args:
            query (str): The research query or question.
            report_type (str): Type of report to generate.
            report_format (str): Format of the report (markdown, pdf, etc).
            report_source (str): Source of information for the report (web, local, etc).
            tone (Tone): Tone of the report.
            source_urls (list[str], optional): List of specific URLs to use as sources.
            document_urls (list[str], optional): List of document URLs to use as sources.
            complement_source_urls (bool): Whether to complement source URLs with web search.
            query_domains (list[str], optional): List of domains to restrict search to.
            documents: Document objects for LangChain integration.
            vector_store: Vector store for document retrieval.
            vector_store_filter: Filter for vector store queries.
            config_path: Path to configuration file.
            websocket: WebSocket for streaming output.
            agent: Pre-defined agent type.
            role: Pre-defined agent role.
            parent_query: Parent query for subtopic reports.
            subtopics: List of subtopics to research.
            visited_urls: Set of already visited URLs.
            verbose (bool): Whether to output verbose logs.
            context: Pre-loaded research context.
            headers (dict, optional): Additional headers for requests and configuration.
            max_subtopics (int): Maximum number of subtopics to generate.
            log_handler: Handler for logging events.
            prompt_family: Family of prompts to use.
            mcp_configs (list[dict], optional): List of MCP server configurations.
                Each dictionary can contain:
                - name (str): Name of the MCP server
                - command (str): Command to start the server
                - args (list[str]): Arguments for the server command
                - tool_name (str): Specific tool to use on the MCP server
                - env (dict): Environment variables for the server
                - connection_url (str): URL for WebSocket or HTTP connection
                - connection_type (str): Connection type (stdio, websocket, http)
                - connection_token (str): Authentication token for remote connections
                
                Example:
                ```python
                mcp_configs=[{
                    "command": "python",
                    "args": ["my_mcp_server.py"],
                    "name": "search"
                }]
                ```
            mcp_strategy (str, optional): MCP execution strategy. Options:
                - "fast" (default): Run MCP once with original query for best performance
                - "deep": Run MCP for all sub-queries for maximum thoroughness  
                - "disabled": Skip MCP entirely, use only web retrievers
        """
        logger.info("▶ GPTResearcher.__init__ — 初始化研究agent | 入参: query=%s, report_type=%s, report_format=%s, report_source=%s, tone=%s, max_subtopics=%d", query, report_type, report_format, report_source, tone, max_subtopics)
        self.kwargs = kwargs
        self.query = query
        self.report_type = report_type
        self.cfg = Config(config_path)
        self.cfg.set_verbose(verbose)
        self.report_source = report_source if report_source else getattr(self.cfg, 'report_source', None)
        self.report_format = report_format
        self.max_subtopics = max_subtopics
        self.tone = tone if isinstance(tone, Tone) else Tone.Objective
        self.source_urls = source_urls
        self.document_urls = document_urls
        self.complement_source_urls = complement_source_urls
        self.query_domains = query_domains or []
        self.research_sources = []  # The list of scraped sources including title, content and images
        self.research_images = []  # The list of selected research images
        self._search_image_metadata: dict[str, dict] = {}  # URL -> metadata for dedicated image search results
        self.documents = documents
        self.vector_store = VectorStoreWrapper(vector_store) if vector_store else None
        self.vector_store_filter = vector_store_filter
        self.websocket = websocket
        self.agent = agent
        self.role = role
        self.parent_query = parent_query
        self.subtopics = subtopics or []
        self.visited_urls = visited_urls or set()
        self.verbose = verbose
        self.context = context or []
        self.headers = headers or {}
        self.research_costs = 0.0
        self.step_costs: dict[str, float] = {}
        self._current_step: str = "general"
        self.log_handler = log_handler
        self.prompt_family = get_prompt_family(prompt_family or self.cfg.prompt_family, self.cfg)
        
        # Process MCP configurations if provided
        self.mcp_configs = mcp_configs
        if mcp_configs:
            self._process_mcp_configs(mcp_configs)
        
        self.retrievers = get_retrievers(self.headers, self.cfg)
        self.memory = Memory(
            self.cfg.embedding_provider, self.cfg.embedding_model, **self.cfg.embedding_kwargs
        )
        
        # Set default encoding to utf-8
        self.encoding = kwargs.get('encoding', 'utf-8')
        self.kwargs.pop('encoding', None)  # Remove encoding from kwargs to avoid passing it to LLM calls

        # Initialize components
        self.research_conductor: ResearchConductor = ResearchConductor(self)
        self.report_generator: ReportGenerator = ReportGenerator(self)
        self.context_manager: ContextManager = ContextManager(self)
        self.scraper_manager: BrowserManager = BrowserManager(self)
        self.source_curator: SourceCurator = SourceCurator(self)
        self.deep_researcher: Optional[DeepResearchSkill] = None
        if report_type == ReportType.DeepResearch.value:
            self.deep_researcher = DeepResearchSkill(self)

        # Initialize image generator (optional - only if configured)
        self.image_generator: Optional[ImageGenerator] = ImageGenerator(self)
        self.available_images: list = []  # Pre-generated images ready for embedding
        self._research_id: str = ""  # Unique ID for this research session

        # Handle MCP strategy configuration with backwards compatibility
        self.mcp_strategy = self._resolve_mcp_strategy(mcp_strategy, mcp_max_iterations)
    
    def _generate_research_id(self) -> str:
        """Generate a unique research ID for this session.
        
        Returns:
            A unique string identifier for this research session.
        """
        logger.info("▶ GPTResearcher._generate_research_id — 生成唯一研究会话ID")
        if not self._research_id:
            import hashlib
            import time
            # Create unique ID from query + timestamp
            unique_str = f"{self.query}_{time.time()}"
            self._research_id = f"research_{hashlib.md5(unique_str.encode()).hexdigest()[:12]}"
        return self._research_id

    def _resolve_mcp_strategy(self, mcp_strategy: str | None, mcp_max_iterations: int | None) -> str:
        """
        Resolve MCP strategy from various sources with backwards compatibility.
        
        Priority:
        1. Parameter mcp_strategy (new approach)
        2. Parameter mcp_max_iterations (backwards compatibility)  
        3. Config MCP_STRATEGY
        4. Default "fast"
        
        Args:
            mcp_strategy: New strategy parameter
            mcp_max_iterations: Legacy parameter for backwards compatibility
            
        Returns:
            str: Resolved strategy ("fast", "deep", or "disabled")
        """
        logger.info("▶ GPTResearcher._resolve_mcp_strategy — 解析MCP策略（含向后兼容） | 入参: mcp_strategy=%s, mcp_max_iterations=%s", mcp_strategy, mcp_max_iterations)
        # Priority 1: Use mcp_strategy parameter if provided
        if mcp_strategy is not None:
            # Support new strategy names
            if mcp_strategy in ["fast", "deep", "disabled"]:
                return mcp_strategy
            # Support old strategy names for backwards compatibility
            elif mcp_strategy == "optimized":
                import logging
                logging.getLogger(__name__).warning("mcp_strategy 'optimized' is deprecated, use 'fast' instead")
                return "fast"
            elif mcp_strategy == "comprehensive":
                import logging
                logging.getLogger(__name__).warning("mcp_strategy 'comprehensive' is deprecated, use 'deep' instead")
                return "deep"
            else:
                import logging
                logging.getLogger(__name__).warning(f"Invalid mcp_strategy '{mcp_strategy}', defaulting to 'fast'")
                return "fast"
        
        # Priority 2: Convert mcp_max_iterations for backwards compatibility
        if mcp_max_iterations is not None:
            import logging
            logging.getLogger(__name__).warning("mcp_max_iterations is deprecated, use mcp_strategy instead")
            
            if mcp_max_iterations == 0:
                return "disabled"
            elif mcp_max_iterations == 1:
                return "fast"
            elif mcp_max_iterations == -1:
                return "deep"
            else:
                # Treat any other number as fast mode
                return "fast"
        
        # Priority 3: Use config setting
        if hasattr(self.cfg, 'mcp_strategy'):
            config_strategy = self.cfg.mcp_strategy
            # Support new strategy names
            if config_strategy in ["fast", "deep", "disabled"]:
                return config_strategy
            # Support old strategy names for backwards compatibility
            elif config_strategy == "optimized":
                return "fast"
            elif config_strategy == "comprehensive":
                return "deep"
            
        # Priority 4: Default to fast
        return "fast"

    def _process_mcp_configs(self, mcp_configs: list[dict]) -> None:
        """
        Process MCP configurations from a list of configuration dictionaries.

        Adds the MCP retriever to the active retriever list by modifying
        self.cfg.retrievers directly.  Deliberately avoids touching os.environ
        so that concurrent or subsequent requests are not affected by this
        session's MCP settings (fixes issue #1676 – process-level env pollution).

        Args:
            mcp_configs (list[dict]): List of MCP server configuration dictionaries.
        """
        logger.info("▶ GPTResearcher._process_mcp_configs — 处理MCP配置 | 入参: mcp_configs数量=%d", len(mcp_configs))
        # Add MCP to retrievers via cfg (not os.environ) to avoid env pollution.
        if hasattr(self.cfg, 'retrievers') and self.cfg.retrievers:
            current_retrievers = (
                list(self.cfg.retrievers)
                if isinstance(self.cfg.retrievers, list)
                else [r.strip() for r in str(self.cfg.retrievers).split(",") if r.strip()]
            )
            if "mcp" not in current_retrievers:
                current_retrievers.append("mcp")
                self.cfg.retrievers = current_retrievers
        else:
            self.cfg.retrievers = ["mcp"]

        # Store the mcp_configs for use by the MCP retriever
        self.mcp_configs = mcp_configs

    async def _log_event(self, event_type: str, **kwargs):
        """Helper method to handle logging events"""
        logger.info("▶ GPTResearcher._log_event — 记录事件 | 入参: event_type=%s", event_type)
        if self.log_handler:
            try:
                if event_type == "tool":
                    await self.log_handler.on_tool_start(kwargs.get('tool_name', ''), **kwargs)
                elif event_type == "action":
                    await self.log_handler.on_agent_action(kwargs.get('action', ''), **kwargs)
                elif event_type == "research":
                    await self.log_handler.on_research_step(kwargs.get('step', ''), kwargs.get('details', {}))

                # Add direct logging as backup
                import logging
                research_logger = logging.getLogger('research')
                research_logger.info(f"{event_type}: {json.dumps(kwargs, default=str)}")

            except Exception as e:
                import logging
                logging.getLogger('research').error(f"Error in _log_event: {e}", exc_info=True)

    async def conduct_research(self, on_progress=None):
        """Conduct the research process.

        This method orchestrates the main research workflow including
        agent selection, web searching, and context gathering.

        Args:
            on_progress: Optional callback for progress updates during deep research.

        Returns:
            The accumulated research context.
        """
        logger.info("▶ GPTResearcher.conduct_research — 执行整个研究流程：搜索、爬取、压缩、生成报告")
        await self._log_event("research", step="start", details={
            "query": self.query,
            "report_type": self.report_type,
            "agent": self.agent,
            "role": self.role
        })

        # Handle deep research separately
        if self.report_type == ReportType.DeepResearch.value and self.deep_researcher:
            self._current_step = "deep_research"
            return await self._handle_deep_research(on_progress)

        if not (self.agent and self.role):
            self._current_step = "agent_selection"
            await self._log_event("action", action="choose_agent")
            # Filter out encoding parameter as it's not supported by LLM APIs
            # filtered_kwargs = {k: v for k, v in self.kwargs.items() if k != 'encoding'}
            self.agent, self.role = await choose_agent(
                query=self.query,
                cfg=self.cfg,
                parent_query=self.parent_query,
                cost_callback=self.add_costs,
                headers=self.headers,
                prompt_family=self.prompt_family,
                **self.kwargs,
                # **filtered_kwargs
            )
            await self._log_event("action", action="agent_selected", details={
                "agent": self.agent,
                "role": self.role
            })

        await self._log_event("research", step="conducting_research", details={
            "agent": self.agent,
            "role": self.role
        })
        self._current_step = "research"
        self.context = await self.research_conductor.conduct_research()

        await self._log_event("research", step="research_completed", details={
            "context_length": len(self.context)
        })
        
        # ----- Dedicated high-quality image search (Unsplash, Pexels, etc.) -----
        # Scraped images from web pages are often low-res thumbnails (300-600px)
        # that look blurry when CSS stretches them to 100% width. Search images
        # from Pexels/Unsplash are guaranteed ≥2MP original quality.
        #
        # To balance quality AND relevance, we extract specific subtopics from
        # the research context and search each one separately — this produces
        # more targeted results than a single broad query.
        image_sources = getattr(self.cfg, 'image_search_sources', '') or ''
        if image_sources.strip() and not (self.image_generator and self.image_generator.is_enabled()):
            try:
                # Build a Tavily image search callback if tavily is in the sources list
                async def _tavily_image_search(query: str, max_results: int = 5) -> list[dict]:
                    from .retrievers.tavily.tavily_search import TavilySearch
                    tav = TavilySearch(query, headers=self.headers)
                    _, images = tav.search(max_results=max_results, include_images=True)
                    return images

                # Extract focused image-search queries from research context.
                # Uses LLM to generate English visual-subject keywords optimized
                # for stock photo APIs (Pexels/Unsplash have English metadata).
                # This is much more effective than raw Chinese section headings.
                image_queries = await _extract_image_search_queries(
                    self.query, self.context, self.cfg, max_queries=10,
                )
                if self.verbose:
                    await stream_output(
                        "logs",
                        "image_search_queries",
                        f"📸 高清图片搜索子主题 ({len(image_queries)}个): {', '.join(image_queries[:10])}",
                        self.websocket,
                    )

                # Search all queries in parallel, track query→image mapping
                all_search_images = []
                seen_img_urls = set()
                tasks = [
                    search_quality_images(q, self.cfg, _tavily_image_search)
                    for q in image_queries
                ]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(batch_results):
                    if isinstance(result, Exception):
                        continue
                    if result:
                        search_query = image_queries[i] if i < len(image_queries) else ""
                        for img in result:
                            if img['url'] not in seen_img_urls:
                                seen_img_urls.add(img['url'])
                                # Tag image with the search query that found it,
                                # so section_hint can be derived later
                                img['_search_query'] = search_query
                                all_search_images.append(img)

                if all_search_images:
                    # Filter images to those whose description/title actually
                    # matches the research topic. This prevents generic sunsets/
                    # landscapes from being embedded in unrelated sections.
                    from ..skills.image_search import filter_relevant_images
                    relevant_images = filter_relevant_images(
                        all_search_images, image_queries, min_overlap=1
                    )
                    if not relevant_images:
                        relevant_images = all_search_images

                    # LLM second-pass: remove images that are visually off-topic
                    # for the research theme (e.g., forest sunset in a fashion report).
                    relevant_images = await _filter_images_by_topic_relevance(
                        relevant_images, self.query, self.cfg
                    )

                    # Preserve metadata so the report writer can caption the
                    # image based on what it actually shows, not the section text.
                    for img in relevant_images:
                        self._search_image_metadata[img['url']] = img

                    # Prepend search images: they're guaranteed high-quality
                    # (≥2MP from Pexels/Unsplash, filtered by API). Scraped
                    # images follow as fillers if needed.
                    search_urls = [img['url'] for img in relevant_images]
                    self.research_images = search_urls + self.research_images
                    if self.verbose:
                        await stream_output(
                            "logs",
                            "image_search_results",
                            f"📸 高清图片搜索完成: {len(all_search_images)} 张 (Pexels/Unsplash/Tavily) "
                            f"| 搜索子主题数: {len(image_queries)}",
                            self.websocket,
                        )
            except Exception as e:
                if self.verbose:
                    await stream_output(
                        "logs",
                        "image_search_error",
                        f"⚠️ 高清图片搜索失败 (将使用网页抓取图片): {e}",
                        self.websocket,
                    )
        
        # Pre-generate images if enabled (happens BEFORE report writing for better UX)
        self.available_images = []
        if self.image_generator and self.image_generator.is_enabled():
            await self._log_event("research", step="planning_images")
            # Convert context list to string for analysis
            context_str = "\n\n".join(self.context) if isinstance(self.context, list) else str(self.context)
            self.available_images = await self.image_generator.plan_and_generate_images(
                context=context_str,
                query=self.query,
                research_id=self._generate_research_id(),
            )
            await self._log_event("research", step="images_pre_generated", details={
                "images_count": len(self.available_images)
            })
        else:
            # 没有 AI 图片生成时，使用网页抓取到的图片嵌入报告
            self.available_images = self._prepare_web_images_for_report()
            if self.available_images and self.verbose:
                await stream_output(
                    "logs",
                    "web_images_ready",
                    f"🖼️ 已收集 {len(self.available_images)} 张网页图片用于嵌入报告",
                    self.websocket,
                )
        
        return self.context

    async def _handle_deep_research(self, on_progress=None):
        """Handle deep research execution and logging.

        Args:
            on_progress: Optional callback for progress updates.

        Returns:
            The accumulated research context from deep research.
        """
        logger.info("▶ GPTResearcher._handle_deep_research — 执行深度研究并记录日志")
        # Log deep research configuration
        await self._log_event("research", step="deep_research_initialize", details={
            "type": "deep_research",
            "breadth": self.deep_researcher.breadth,
            "depth": self.deep_researcher.depth,
            "concurrency": self.deep_researcher.concurrency_limit
        })

        # Log deep research start
        await self._log_event("research", step="deep_research_start", details={
            "query": self.query,
            "breadth": self.deep_researcher.breadth,
            "depth": self.deep_researcher.depth,
            "concurrency": self.deep_researcher.concurrency_limit
        })

        # Run deep research and get context
        self.context = await self.deep_researcher.run(on_progress=on_progress)

        # Get total research costs
        total_costs = self.get_costs()

        # Log deep research completion with costs
        await self._log_event("research", step="deep_research_complete", details={
            "context_length": len(self.context),
            "visited_urls": len(self.visited_urls),
            "total_costs": total_costs
        })

        # Log final cost update
        await self._log_event("research", step="cost_update", details={
            "cost": total_costs,
            "total_cost": total_costs,
            "research_type": "deep_research"
        })

        # Return the research context
        return self.context

    async def write_report(
        self,
        existing_headers: list = [],
        relevant_written_contents: list = [],
        ext_context=None,
        custom_prompt="",
    ) -> str:
        """Write the research report.

        Args:
            existing_headers: List of existing headers to avoid duplication.
            relevant_written_contents: List of previously written content for context.
            ext_context: External context to use instead of internal context.
            custom_prompt: Custom prompt to guide report generation.

        Returns:
            The generated report as a string.
        """
        logger.info("▶ GPTResearcher.write_report — 根据收集的上下文生成研究报告 | 入参: ext_context=%s, custom_prompt=%s", ext_context is not None, custom_prompt)
        # Use pre-generated images if available (generated during conduct_research)
        has_available_images = bool(self.available_images)
        
        self._current_step = "report_writing"
        await self._log_event("research", step="writing_report", details={
            "existing_headers": existing_headers,
            "context_source": "external" if ext_context else "internal",
            "available_images_count": len(self.available_images),
        })

        # Generate report with available images embedded
        report = await self.report_generator.write_report(
            existing_headers=existing_headers,
            relevant_written_contents=relevant_written_contents,
            ext_context=ext_context or self.context,
            custom_prompt=custom_prompt,
            available_images=self.available_images,  # Pass pre-generated images
        )

        await self._log_event("research", step="report_completed", details={
            "report_length": len(report),
            "images_embedded": len(self.available_images) if has_available_images else 0,
        })
        return report

    async def write_report_conclusion(self, report_body: str) -> str:
        """Write the conclusion section of the report.

        Args:
            report_body: The main body of the report to conclude.

        Returns:
            The generated conclusion text.
        """
        logger.info("▶ GPTResearcher.write_report_conclusion — 撰写报告的结论部分 | 入参: report_body长度=%d", len(report_body) if report_body else 0)
        await self._log_event("research", step="writing_conclusion")
        conclusion = await self.report_generator.write_report_conclusion(report_body)
        await self._log_event("research", step="conclusion_completed")
        return conclusion

    async def write_introduction(self) -> str:
        """Write the introduction section of the report.

        Returns:
            The generated introduction text.
        """
        logger.info("▶ GPTResearcher.write_introduction — 撰写报告的引言部分")
        await self._log_event("research", step="writing_introduction")
        intro = await self.report_generator.write_introduction()
        await self._log_event("research", step="introduction_completed")
        return intro

    async def quick_search(
        self,
        query: str,
        query_domains: list[str] = None,
        aggregated_summary: bool = False,
        all_retrievers: bool = False,
    ) -> list[Any] | str:
        """Perform a quick search without full research workflow.

        Args:
            query: The search query.
            query_domains: Optional list of domains to restrict search to.
            aggregated_summary: Whether to return an aggregated summary of the search results.
            all_retrievers: If True, query every configured retriever concurrently and
                merge the results (de-duplicated by URL). Defaults to False, which uses
                only the primary retriever for backward compatibility.

        Returns:
            List of search results or a synthesized summary string.
        """
        logger.info("▶ GPTResearcher.quick_search — 执行快速搜索（不运行完整研究流程） | 入参: query=%s, aggregated_summary=%s, all_retrievers=%s", query, aggregated_summary, all_retrievers)
        if all_retrievers and len(self.retrievers) > 1:
            search_results = await self._search_all_retrievers(query, query_domains)
        else:
            search_results = await get_search_results(
                query, self.retrievers[0], query_domains=query_domains, researcher=self
            )

        if not aggregated_summary:
            return search_results

        # Format results for summary. Search retrievers return records keyed
        # by "href" (URL) and "body" (content); fall back to the alternate
        # keys so callers that pass pre-normalized records still work.
        context = ""
        for i, result in enumerate(search_results, 1):
            title = result.get("title", "")
            body = result.get("body") or result.get("content", "")
            url = result.get("href") or result.get("url", "")
            context += f"[{i}] {title}: {body} ({url})\n\n"

        prompt = self.prompt_family.generate_quick_summary_prompt(query, context)

        summary = await create_chat_completion(
            model=self.cfg.smart_llm_model,
            messages=[{"role": "user", "content": prompt}],
            llm_provider=self.cfg.smart_llm_provider,
            max_tokens=self.cfg.smart_token_limit,
            llm_kwargs=self.cfg.llm_kwargs,
            cost_callback=self.add_costs
        )

        return summary

    async def _search_all_retrievers(
        self, query: str, query_domains: list[str] = None
    ) -> list[dict[str, Any]]:
        """Query every configured retriever concurrently and merge the results.

        Results are de-duplicated by URL (checking both ``url`` and ``href`` keys,
        which different retrievers use). Retrievers that raise are skipped so a
        single failing provider does not abort the whole search.

        Args:
            query: The search query.
            query_domains: Optional list of domains to restrict search to.

        Returns:
            A merged, de-duplicated list of search results.
        """
        logger.info("▶ GPTResearcher._search_all_retrievers — 并发查询所有检索器并合并去重结果 | 入参: query=%s", query)
        tasks = [
            get_search_results(query, retriever, query_domains=query_domains, researcher=self)
            for retriever in self.retrievers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for result in results:
            if isinstance(result, Exception) or not result:
                continue
            for item in result:
                url = item.get("url") or item.get("href") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                merged.append(item)
        return merged

    async def get_subtopics(self):
        """Generate subtopics for the research query.

        Returns:
            List of generated subtopics.
        """
        logger.info("▶ GPTResearcher.get_subtopics — 为研究查询生成子主题列表")
        return await self.report_generator.get_subtopics()

    async def get_draft_section_titles(self, current_subtopic: str) -> list[str]:
        """Generate draft section titles for a subtopic.

        Args:
            current_subtopic: The subtopic to generate sections for.

        Returns:
            List of section title strings.
        """
        logger.info("▶ GPTResearcher.get_draft_section_titles — 为子主题生成草稿章节标题 | 入参: current_subtopic=%s", current_subtopic)
        return await self.report_generator.get_draft_section_titles(current_subtopic)

    async def get_similar_written_contents_by_draft_section_titles(
        self,
        current_subtopic: str,
        draft_section_titles: list[str],
        written_contents: list[dict],
        max_results: int = 10
    ) -> list[str]:
        """Find similar previously written contents based on section titles.

        Args:
            current_subtopic: The current subtopic being written.
            draft_section_titles: List of draft section titles.
            written_contents: Previously written content to search through.
            max_results: Maximum number of results to return.

        Returns:
            List of similar content strings.
        """
        logger.info("▶ GPTResearcher.get_similar_written_contents_by_draft_section_titles — 根据章节标题查找相似的已写内容 | 入参: current_subtopic=%s, max_results=%d", current_subtopic, max_results)
        return await self.context_manager.get_similar_written_contents_by_draft_section_titles(
            current_subtopic,
            draft_section_titles,
            written_contents,
            max_results
        )

    # Utility methods
    def get_research_images(self, top_k: int = 10) -> list[dict[str, Any]]:
        """Get the top research images collected during research.

        Args:
            top_k: Maximum number of images to return.

        Returns:
            List of image dictionaries.
        """
        logger.info("▶ GPTResearcher.get_research_images — 获取研究过程中收集的图片 | 入参: top_k=%d", top_k)
        return self.research_images[:top_k]

    def add_research_images(self, images: list[dict[str, Any]]) -> None:
        """Add images to the research image collection.

        Args:
            images: List of image dictionaries to add.
        """
        logger.info("▶ GPTResearcher.add_research_images — 向研究图片集合中添加图片 | 入参: images数量=%d", len(images))
        self.research_images.extend(images)

    def _prepare_web_images_for_report(self) -> list[dict[str, Any]]:
        """将网页抓取到的图片转换为报告可嵌入的格式。
        
        从 self.research_images（爬虫收集的图片 URL 列表）和 
        self.research_sources（包含标题的源数据）中构建可供 
        report writer 使用的 available_images 格式。
        
        Returns:
            List of image dicts with url, title, alt_text, section_hint, 
            dimensions, quality keys.
        """
        logger.info("▶ GPTResearcher._prepare_web_images_for_report — 将网页抓取图片转换为报告可嵌入格式")
        available = []
        seen_keys = set()  # Using (normalized_url, domain+filename) for robust dedup

        # 构建 URL -> source title 的映射
        source_title_map = {}
        for source in self.research_sources:
            src_url = source.get("url", "")
            src_title = source.get("title", "")
            if src_url and src_title:
                source_title_map[src_url] = src_title

        # 过滤掉明显不适合嵌入的图片（SVG图标、占位图、base64小图等）
        def _is_valid_image(url: str) -> bool:
            if not url:
                return False
            url_lower = url.lower()
            # 排除常见的小图标和占位图
            skip_patterns = [
                "favicon", "icon-", "-icon", "logo-", "-logo",
                "placeholder", "1x1", "pixel", "spacer", "blank",
                ".svg",  # SVG 通常是图标，不适合作为内容插图
                "data:image/svg",
            ]
            for pattern in skip_patterns:
                if pattern in url_lower:
                    return False
            # 只接受可识别的图片格式
            valid_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
            # 也接受无扩展名但从CDN来的图片
            if any(ext in url_lower for ext in valid_extensions) or any(
                domain in url_lower for domain in ["wp.com", "wordpress.com", "cloudinary.com", "imgur.com"]
            ):
                return True
            return True  # 不确定的情况下允许通过

        # 生成去重键：标准化URL + 域名+文件名组合
        def _dedup_key(img_url: str) -> tuple[str, str]:
            from urllib.parse import urlparse as _urlparse
            normalized = normalize_image_url(img_url)
            # 二级键：域名+文件名最后一段，防止同一图片不同CDN URL
            try:
                parsed = _urlparse(img_url)
                filename = parsed.path.rstrip('/').split('/')[-1] if parsed.path else ''
                domain_key = f"{parsed.netloc}/{filename}" if filename else normalized
            except Exception:
                domain_key = normalized
            return (normalized, domain_key)

        # 取前 10 张有效且不重复的图片用于嵌入报告（给LLM更多选择）
        # 只使用搜索图片，爬虫图片不再作为内容图（避免无关/低质图片混进来）
        search_image_urls = [
            url for url in self.research_images
            if url in self._search_image_metadata
        ]
        for img_url in search_image_urls:
            if len(available) >= 10:
                break
            if not _is_valid_image(img_url):
                continue

            # 使用标准化URL + 域名文件名组合进行去重
            norm_key, domain_key = _dedup_key(img_url)
            if norm_key in seen_keys or domain_key in seen_keys:
                continue
            seen_keys.add(norm_key)
            seen_keys.add(domain_key)

            # 只使用搜索图片的真实描述
            search_meta = self._search_image_metadata.get(img_url, {})
            if not search_meta:
                continue

            matched_title = (
                search_meta.get("description")
                or search_meta.get("title")
                or ""
            )

            # 丢弃没有有效描述的图片（如描述为空、只有作者名、或太泛）
            if not matched_title or _is_generic_description(matched_title):
                continue

            # 使用真实来源补充信息
            source = search_meta.get("source", "")
            if source:
                matched_title = matched_title.strip()

            # —— 生成精准的 section_hint ——
            # 优先级: 搜索查询 > 描述关键词 > "General"
            search_query = search_meta.get('_search_query', '')
            section_hint = _derive_section_hint(matched_title, search_query)

            alt_text = f"插图: {matched_title}"

            # Build rich metadata for report writer
            img_entry = {
                "url": img_url,
                "title": matched_title,
                "alt_text": alt_text,
                "section_hint": section_hint,
            }
            # Add dimension info if available from search metadata
            if search_meta.get("width") and search_meta.get("height"):
                img_entry["dimensions"] = f"{search_meta['width']}x{search_meta['height']}"
            if source:
                img_entry["quality"] = "高清" if source in ("pexels", "unsplash") else "标准"

            available.append(img_entry)
        
        return available

    def get_research_sources(self) -> list[dict[str, Any]]:
        """Get all research sources collected during research.

        Returns:
            List of source dictionaries containing title, content, and images.
        """
        logger.info("▶ GPTResearcher.get_research_sources — 获取研究过程中收集的所有来源")
        return self.research_sources

    def add_research_sources(self, sources: list[dict[str, Any]]) -> None:
        """Add sources to the research source collection.

        Args:
            sources: List of source dictionaries to add.
        """
        logger.info("▶ GPTResearcher.add_research_sources — 向研究来源集合中添加来源 | 入参: sources数量=%d", len(sources))
        self.research_sources.extend(sources)

    def add_references(self, report_markdown: str, visited_urls: set) -> str:
        """Add reference section to a markdown report.

        Args:
            report_markdown: The markdown report text.
            visited_urls: Set of URLs to include as references.

        Returns:
            The report with references appended.
        """
        logger.info("▶ GPTResearcher.add_references — 为报告添加参考文献部分 | 入参: visited_urls数量=%d", len(visited_urls))
        return add_references(report_markdown, visited_urls)

    def extract_headers(self, markdown_text: str) -> list[dict]:
        """Extract headers from markdown text.

        Args:
            markdown_text: The markdown text to parse.

        Returns:
            List of header dictionaries.
        """
        logger.info("▶ GPTResearcher.extract_headers — 从Markdown文本中提取标题")
        return extract_headers(markdown_text)

    def extract_sections(self, markdown_text: str) -> list[dict]:
        """Extract sections from markdown text.

        Args:
            markdown_text: The markdown text to parse.

        Returns:
            List of section dictionaries.
        """
        logger.info("▶ GPTResearcher.extract_sections — 从Markdown文本中提取章节")
        return extract_sections(markdown_text)

    def table_of_contents(self, markdown_text: str) -> str:
        """Generate a table of contents for markdown text.

        Args:
            markdown_text: The markdown text to generate TOC for.

        Returns:
            The table of contents as markdown string.
        """
        logger.info("▶ GPTResearcher.table_of_contents — 为Markdown文本生成目录")
        return table_of_contents(markdown_text)

    def get_source_urls(self) -> list:
        """Get all visited source URLs.

        Returns:
            List of visited URL strings.
        """
        logger.info("▶ GPTResearcher.get_source_urls — 获取所有已访问的源URL")
        return list(self.visited_urls)

    def get_research_context(self) -> list:
        """Get the accumulated research context.

        Returns:
            List of context items collected during research.
        """
        logger.info("▶ GPTResearcher.get_research_context — 获取累积的研究上下文")
        return self.context

    def get_costs(self) -> float:
        """Get the total accumulated API costs.

        Returns:
            Total cost in USD.
        """
        logger.info("▶ GPTResearcher.get_costs — 获取累计API费用")
        return self.research_costs

    def get_step_costs(self) -> dict[str, float]:
        """Get a breakdown of API costs per research step.

        Returns:
            Dictionary mapping step names to their costs in USD.
        """
        logger.info("▶ GPTResearcher.get_step_costs — 获取各研究步骤的API费用明细")
        return dict(self.step_costs)

    def set_verbose(self, verbose: bool) -> None:
        """Set the verbose output mode.

        Args:
            verbose: Whether to enable verbose output.
        """
        logger.info("▶ GPTResearcher.set_verbose — 设置详细输出模式 | 入参: verbose=%s", verbose)
        self.verbose = verbose

    def add_costs(self, cost: float) -> None:
        """Add to the accumulated API costs.

        The cost is attributed to the current step set via ``_current_step``.

        Args:
            cost: Cost amount to add in USD.

        Raises:
            ValueError: If cost is not a number.
        """
        logger.info("▶ GPTResearcher.add_costs — 累加API费用 | 入参: cost=%.6f", cost)
        if not isinstance(cost, (float, int)):
            raise ValueError("Cost must be an integer or float")
        self.research_costs += cost
        step = self._current_step
        self.step_costs[step] = self.step_costs.get(step, 0.0) + cost
        if self.log_handler:
            self._log_event("research", step="cost_update", details={
                "cost": cost,
                "total_cost": self.research_costs,
                "step_name": step,
            })


async def _extract_image_search_queries(
    query: str,
    context,
    cfg,
    max_queries: int = 10,
) -> list[str]:
    """Extract visual image search queries from research context.

    Uses LLM to analyze the research sections and generate short English
    search queries optimized for stock photo APIs (Pexels, Unsplash). One
    query is generated PER section/subtopic so that every paragraph can
    get a relevant photo. The queries target visual subject matter
    (aesthetics, objects, scenes, activities) rather than specific people
    or brand names.

    Args:
        query: The original research query.
        context: Research context (list of strings or single string).
        cfg: Config object for LLM access.
        max_queries: Maximum number of distinct search queries to generate.

    Returns:
        List of English image search query strings, length ≤ max_queries.
    """
    import re
    from ..utils.llm import create_chat_completion as _chat_completion

    context_str = "\n".join(context) if isinstance(context, list) else str(context)
    context_str = context_str[:6000]

    try:
        prompt = (
            f"I am researching: \"{query}\"\n\n"
            f"Below is the research summary. Identify the key sections/paragraphs "
            f"and for EACH one, generate a short English search phrase that would "
            f"find a relevant stock photo on Unsplash/Pexels.\n\n"
            f"Rules:\n"
            f"- One query per distinct section/topic in the research.\n"
            f"- Focus on VISUAL subjects: objects, scenes, aesthetics, activities, "
            f"environments, fashion styles, food, technology, nature, business settings, etc.\n"
            f"- Use concise English (1-5 words). Example: 'modern office workspace'. "
            f"Example: 'elegant red carpet fashion'. Example: 'AI chip circuit board'.\n"
            f"- For people/brand topics: search the visual style NOT the name "
            f"(e.g., 'luxury fashion runway' not 'Liu Yifei').\n"
            f"- Aim for {max_queries} queries covering all major sections.\n"
            f"- Each line = one query. NO numbering, NO bullet points, NO explanation.\n\n"
            f"Research summary:\n{context_str}\n\n"
            f"Image search queries (one per line):"
        )

        response = await _chat_completion(
            model=cfg.smart_llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            llm_provider=cfg.smart_llm_provider,
            max_tokens=300,
            llm_kwargs=cfg.llm_kwargs,
        )

        llm_queries = [
            q.strip().lstrip('*-•·1234567890.) ')
            for q in response.strip().split('\n')
            if q.strip() and len(q.strip()) >= 3
        ]

        # Deduplicate and limit
        seen = set()
        queries = []
        for q in llm_queries:
            q_lower = q.lower()
            if q_lower not in seen:
                seen.add(q_lower)
                queries.append(q)
                if len(queries) >= max_queries:
                    break

        if queries:
            return queries
    except Exception:
        pass  # Fall through to regex-based extraction

    # Regex-based fallback when LLM is unavailable
    queries = [query]
    if context:
        headings = re.findall(r'#{1,3}\s+(.+?)(?:\n|$)', context_str)
        numbered = re.findall(
            r'(?:^|\n)\s*(?:\d+[\.\)]\s*)([A-Z][^\n]{10,80})(?:\n|$)',
            context_str,
        )
        skip = {
            "introduction", "conclusion", "references", "summary",
            "appendix", "abstract", "table of contents", "overview",
            "background", "methodology", "further reading",
            "introducción", "conclusión", "referencias", "resumen",
        }
        candidates = []
        for h in headings + numbered:
            h = h.strip()
            if h.lower() in skip:
                continue
            if 5 <= len(h) <= 100:
                candidates.append(h)
        for candidate in candidates:
            if len(queries) >= max_queries:
                break
            if not any(_similar_query(candidate, q) for q in queries):
                queries.append(candidate)
    return queries[:max_queries]


def _similar_query(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two queries are semantically similar (simple word overlap)."""
    # Simple word-overlap similarity
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return False
    small, large = sorted([words_a, words_b], key=len)
    overlap = len(small & large)
    return overlap / len(small) >= threshold


def _is_generic_description(text: str) -> bool:
    """Return True if the image description is too generic to be useful.

    Generic descriptions like 'Pexels photo by John', 'Photo', 'Illustration'
    or empty strings don't tell the LLM what the image actually shows, so
    they should be discarded to avoid random/irrelevant images being placed.
    """
    if not text or not text.strip():
        return True
    text = text.strip().lower()

    generic_patterns = [
        "photo by",
        "photography by",
        "pexels photo",
        "unsplash photo",
        "tavily photo",
        "photo on",
        "photographer",
        "image by",
        "picture by",
        "stock photo",
        "related illustration",
        "相关插图",
        "插图",
        "illustration",
        "photo",
        "image",
        "picture",
    ]
    # Count how many meaningful words are in the description
    meaningful_words = [
        w for w in re.findall(r'[a-z\u4e00-\u9fff]+', text)
        if w not in {
            "photo", "by", "pexels", "unsplash", "tavily", "image", "of",
            "the", "a", "an", "in", "on", "at", "and", "or", "with", "from",
            "for", "as", "to", "is", "are", "photography", "photographer",
            "picture", "shot", "illustration", "stock", "curated", "high",
            "quality", "resolution", "hd", "4k", "free", "download", "相关",
            "插图",
        }
    ]
    # If description is just a generic pattern with no meaningful content, it's generic
    if len(meaningful_words) < 2:
        return True
    # If the whole text matches a generic pattern closely, it's generic
    if any(text.startswith(p) and len(meaningful_words) < 3 for p in generic_patterns):
        return True
    return False


async def _filter_images_by_topic_relevance(
    images: list[dict],
    topic: str,
    cfg,
) -> list[dict]:
    """Use LLM to filter out images that are not relevant to the research topic.

    Even with keyword filtering, stock photo APIs can return near-matches that
    are off-topic (e.g., a generic forest photo for a luxury fashion report).
    This function asks an LLM to judge each image's relevance based on its
    description and the research topic.

    Args:
        images: Image dicts with at least 'description'/'title' and 'url'.
        topic: The research topic/query.
        cfg: Config object for LLM access.

    Returns:
        List of images the LLM judges as relevant to the topic.
    """
    from ..utils.llm import create_chat_completion as _chat_completion

    if not images:
        return []

    # Only run the LLM filter if we have enough candidates to make it worthwhile
    # and we have a clear topic. Small lists are likely already relevant.
    if len(images) <= 3:
        return images

    # Build a compact list of images for the LLM to judge
    image_lines = []
    for i, img in enumerate(images):
        desc = img.get('description') or img.get('title') or 'Unknown image'
        # Keep description concise
        desc = desc[:120]
        image_lines.append(f"{i+1}. {desc}")
    image_list = "\n".join(image_lines)

    prompt = (
        f"Research topic: \"{topic}\"\n\n"
        f"Below are candidate stock photos. For each one, reply ONLY with the numbers "
        f"of images that are DIRECTLY RELEVANT to the topic. If none are relevant, "
        f"reply with the word NONE.\n\n"
        f"Rules:\n"
        f"- A photo is relevant ONLY if it visually matches the subject matter of the topic.\n"
        f"- Generic nature scenes (forests, sunsets, beaches, mountains) are NOT "
        f"relevant for fashion, celebrity, luxury, technology, or business topics.\n"
        f"- Abstract or unrelated objects (e.g., a coffee cup for a car review) are NOT relevant.\n"
        f"- Reply with numbers separated by commas, e.g., '1, 3, 5' or 'NONE'.\n\n"
        f"Images:\n{image_list}\n\n"
        f"Relevant image numbers:"
    )

    try:
        response = await _chat_completion(
            model=cfg.smart_llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            llm_provider=cfg.smart_llm_provider,
            max_tokens=100,
            llm_kwargs=cfg.llm_kwargs,
        )

        response = response.strip().lower()
        if not response or response in {"none", "无", "没有"}:
            return []

        # Parse numbers from response
        selected_indices = set()
        for token in re.findall(r'\d+', response):
            try:
                idx = int(token) - 1  # Convert from 1-based to 0-based
                if 0 <= idx < len(images):
                    selected_indices.add(idx)
            except ValueError:
                continue

        if selected_indices:
            return [images[i] for i in sorted(selected_indices)]
    except Exception:
        pass

    # If LLM fails, return all images (downstream filters still apply)
    return images
    """Derive a section placement hint from image metadata.

    Uses the search query that found the image and the image's own
    description to generate a short hint telling the report writer
    which section/paragraph this image likely matches.

    Args:
        title: Image description/title from the API.
        search_query: The search query that returned this image.

    Returns:
        A short section hint string (e.g. "Fashion", "Technology", "Nature").
    """
    import re

    # Priority 1: Use the search query as the most direct hint
    if search_query and len(search_query) >= 3:
        # Clean up the query: remove common prefixes, capitalize words
        hint = search_query.strip()
        # Remove query operators sometimes added by APIs
        hint = re.sub(r'\b(AND|OR|NOT)\b', '', hint, flags=re.IGNORECASE)
        hint = re.sub(r'\s+', ' ', hint).strip()
        if len(hint) <= 40:
            return hint.title()
        return ' '.join(w.title() for w in hint.split()[:4])

    # Priority 2: Extract key visual nouns from the title
    if title and title not in ("相关插图", "Pexels photo", "Unsplash photo"):
        # Extract meaningful words (nouns, adjectives)
        words = [w for w in title.lower().split()
                 if w not in {"a", "an", "the", "of", "by", "in", "on", "at",
                              "and", "or", "with", "from", "for", "is", "are",
                              "photo", "image", "photography", "photographer",
                              "pexels", "unsplash", "stock", "free"}]
        if words:
            hint = ' '.join(w.title() for w in words[:5])
            if len(hint) <= 50:
                return hint

    return "General"

