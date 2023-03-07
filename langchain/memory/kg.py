from typing import Any, Dict, List

from pydantic import BaseModel, Field

from langchain.chains.llm import LLMChain
from langchain.graphs import NetworkxEntityGraph
from langchain.graphs.networkx_graph import get_entities, parse_triples
from langchain.llms.base import BaseLLM
from langchain.memory.chat_memory import BaseChatMemory
from langchain.memory.prompt import (
    ENTITY_EXTRACTION_PROMPT,
    KNOWLEDGE_TRIPLE_EXTRACTION_PROMPT,
)
from langchain.memory.utils import get_buffer_string, get_prompt_input_key
from langchain.prompts.base import BasePromptTemplate
from langchain.schema import SystemMessage


class ConversationKGMemory(BaseChatMemory, BaseModel):
    """Knowledge graph memory for storing conversation memory.

    Integrates with external knowledge graph to store and retrieve
    information about knowledge triples in the conversation.
    """

    k: int = 2
    human_prefix: str = "Human"
    ai_prefix: str = "AI"
    kg: NetworkxEntityGraph = Field(default_factory=NetworkxEntityGraph)
    knowledge_extraction_prompt: BasePromptTemplate = KNOWLEDGE_TRIPLE_EXTRACTION_PROMPT
    entity_extraction_prompt: BasePromptTemplate = ENTITY_EXTRACTION_PROMPT
    llm: BaseLLM
    """Number of previous utterances to include in the context."""
    memory_key: str = "history"  #: :meta private:

    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Return history buffer."""
        entities = self._get_current_entities(inputs)
        summaries = {}
        for entity in entities:
            knowledge = self.kg.get_entity_knowledge(entity)
            if knowledge:
                summaries[entity] = ". ".join(knowledge) + "."
        if summaries:
            summary_strings = [
                f"On {entity}: {summary}" for entity, summary in summaries.items()
            ]
            if self.return_messages:
                context: Any = [SystemMessage(content=text) for text in summary_strings]
            else:
                context = "\n".join(summary_strings)
        else:
            if self.return_messages:
                context = []
            else:
                context = ""
        return {self.memory_key: context}

    @property
    def memory_variables(self) -> List[str]:
        """Will always return list of memory variables.

        :meta private:
        """
        return [self.memory_key]

    def _get_prompt_input_key(self, inputs: Dict[str, Any]) -> str:
        """Get the input key for the prompt."""
        if self.input_key is None:
            return get_prompt_input_key(inputs, self.memory_variables)
        return self.input_key

    def _get_prompt_output_key(self, outputs: Dict[str, Any]) -> str:
        """Get the output key for the prompt."""
        if self.output_key is None:
            if len(outputs) != 1:
                raise ValueError(f"One output key expected, got {outputs.keys()}")
            return list(outputs.keys())[0]
        return self.output_key

    def _get_current_entities(self, inputs: Dict[str, Any]) -> List[str]:
        """Get the current entities in the conversation."""
        prompt_input_key = self._get_prompt_input_key(inputs)
        chain = LLMChain(llm=self.llm, prompt=self.entity_extraction_prompt)
        buffer_string = get_buffer_string(
            self.chat_memory.messages[-self.k * 2 :],
            human_prefix=self.human_prefix,
            ai_prefix=self.ai_prefix,
        )
        output = chain.predict(
            history=buffer_string,
            input=inputs[prompt_input_key],
        )
        return get_entities(output)

    def _get_and_update_kg(self, inputs: Dict[str, Any]) -> None:
        """Get and update knowledge graph from the conversation history."""
        chain = LLMChain(llm=self.llm, prompt=self.knowledge_extraction_prompt)
        prompt_input_key = self._get_prompt_input_key(inputs)
        buffer_string = get_buffer_string(
            self.chat_memory.messages[-self.k * 2 :],
            human_prefix=self.human_prefix,
            ai_prefix=self.ai_prefix,
        )
        output = chain.predict(
            history=buffer_string,
            input=inputs[prompt_input_key],
            verbose=True,
        )
        knowledge = parse_triples(output)
        for triple in knowledge:
            self.kg.add_triple(triple)

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
        """Save context from this conversation to buffer."""
        super().save_context(inputs, outputs)
        self._get_and_update_kg(inputs)

    def clear(self) -> None:
        """Clear memory contents."""
        super().clear()
        self.kg.clear()