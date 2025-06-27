# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import json
import logging
from typing import Dict, List, Optional, Tuple, Union

from langchain.callbacks.manager import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_community.tools.tavily_search.tool import TavilySearchResults
from pydantic import Field

from src.tools.tavily_search.tavily_search_api_wrapper import (
    EnhancedTavilySearchAPIWrapper,
)

logger = logging.getLogger(__name__)


class TavilySearchResultsWithImages(TavilySearchResults):  # type: ignore[override, override]
    """Tool that queries the Tavily Search API and gets back json.

    Setup:
        Install ``langchain-openai`` and ``tavily-python``, and set environment variable ``TAVILY_API_KEY``.

        .. code-block:: bash

            pip install -U langchain-community tavily-python
            export TAVILY_API_KEY="your-api-key"

    Instantiate:

        .. code-block:: python

            from langchain_community.tools import TavilySearchResults

            tool = TavilySearchResults(
                max_results=5,
                include_answer=True,
                include_raw_content=True,
                include_images=True,
                include_image_descriptions=True,
                # search_depth="advanced",
                # include_domains = []
                # exclude_domains = []
            )

    Invoke directly with args:

        .. code-block:: python

            tool.invoke({'query': 'who won the last french open'})

        .. code-block:: json

            {
                "url": "https://www.nytimes.com...",
                "content": "Novak Djokovic won the last French Open by beating Casper Ruud ..."
            }

    Invoke with tool call:

        .. code-block:: python

            tool.invoke({"args": {'query': 'who won the last french open'}, "type": "tool_call", "id": "foo", "name": "tavily"})

        .. code-block:: python

            ToolMessage(
                content='{ "url": "https://www.nytimes.com...", "content": "Novak Djokovic won the last French Open by beating Casper Ruud ..." }',
                artifact={
                    'query': 'who won the last french open',
                    'follow_up_questions': None,
                    'answer': 'Novak ...',
                    'images': [
                        'https://www.amny.com/wp-content/uploads/2023/06/AP23162622181176-1200x800.jpg',
                        ...
                        ],
                    'results': [
                        {
                            'title': 'Djokovic ...',
                            'url': 'https://www.nytimes.com...',
                            'content': "Novak...",
                            'score': 0.99505633,
                            'raw_content': 'Tennis\nNovak ...'
                        },
                        ...
                    ],
                    'response_time': 2.92
                },
                tool_call_id='1',
                name='tavily_search_results_json',
            )

    """  # noqa: E501

    include_image_descriptions: bool = False
    """Include a image descriptions in the response.

    Default is False.
    """

    api_wrapper: EnhancedTavilySearchAPIWrapper = Field(
        default_factory=EnhancedTavilySearchAPIWrapper
    )  # type: ignore[arg-type]

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Tuple[Union[List[Dict[str, str]], str], Dict]:
        """Use the tool."""
        # TODO: remove try/except, should be handled by BaseTool
        try:
            raw_results = self.api_wrapper.raw_results(
                query,
                self.max_results,
                self.search_depth,
                self.include_domains,
                self.exclude_domains,
                self.include_answer,
                self.include_raw_content,
                self.include_images,
                self.include_image_descriptions,
            )
        except Exception as e:
            return repr(e), {}
        cleaned_results = self.api_wrapper.clean_results_with_images(raw_results)

        # 記錄詳細的結果信息
        logger.debug(f"Tavily search sync - Raw results type: {type(raw_results)}")
        logger.debug(f"Tavily search sync - Cleaned results type: {type(cleaned_results)}")

        try:
            results_json = json.dumps(cleaned_results, indent=2, ensure_ascii=False)
            logger.info(f"Tavily search sync results: {results_json}")
            print("sync", results_json)  # 保持控制台輸出
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize Tavily search results to JSON: {e}")
            logger.error(f"Cleaned results: {cleaned_results}")
            # 如果 JSON 序列化失敗，返回字符串表示
            fallback_result = str(cleaned_results)
            logger.info(f"Tavily search sync results (fallback): {fallback_result}")
            print("sync", fallback_result)

        return cleaned_results, raw_results

    async def _arun(
        self,
        query: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> Tuple[Union[List[Dict[str, str]], str], Dict]:
        """Use the tool asynchronously."""
        try:
            raw_results = await self.api_wrapper.raw_results_async(
                query,
                self.max_results,
                self.search_depth,
                self.include_domains,
                self.exclude_domains,
                self.include_answer,
                self.include_raw_content,
                self.include_images,
                self.include_image_descriptions,
            )
        except Exception as e:
            return repr(e), {}
        cleaned_results = self.api_wrapper.clean_results_with_images(raw_results)

        # 記錄詳細的結果信息
        logger.debug(f"Tavily search async - Raw results type: {type(raw_results)}")
        logger.debug(f"Tavily search async - Cleaned results type: {type(cleaned_results)}")

        try:
            results_json = json.dumps(cleaned_results, indent=2, ensure_ascii=False)
            logger.info(f"Tavily search async results: {results_json}")
            # print("async", results_json)  # 保持控制台輸出  #TODO: 目前只有這一行輸出，暫時 mark 掉
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize Tavily search async results to JSON: {e}")
            logger.error(f"Cleaned results: {cleaned_results}")
            # 如果 JSON 序列化失敗，返回字符串表示
            fallback_result = str(cleaned_results)
            logger.info(f"Tavily search async results (fallback): {fallback_result}")
            print("async", fallback_result)

        return cleaned_results, raw_results
