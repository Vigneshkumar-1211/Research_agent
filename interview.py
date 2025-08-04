
from typing import List
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langgraph.graph import START, END, StateGraph

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from prompts import analyst_instructions, question_instructions, search_instructions, answer_instructions, section_writer_instructions

import operator
from typing import  Annotated
from langgraph.graph import MessagesState

from langchain_core.messages import get_buffer_string

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.document_loaders import WikipediaLoader


class Analyst(BaseModel):
    affiliation: str = Field(
        description="Primary affiliation of the analyst.",
    )
    name: str = Field(
        description="Name of the analyst."
    )
    role: str = Field(
        description="Role of the analyst in the context of the topic.",
    )
    description: str = Field(
        description="Description of the analyst focus, concerns, and motives.",
    )
    @property
    def persona(self) -> str:
        return f"Name: {self.name}\nRole: {self.role}\nAffiliation: {self.affiliation}\nDescription: {self.description}\n"


class Perspectives(BaseModel):
    analysts: List[Analyst] = Field(
        description="Comprehensive list of analysts with their roles and affiliations.",
    )


class GenerateAnalystsState(TypedDict):
    topic: str
    max_analysts: int
    human_analyst_feedback: str
    analysts: List[Analyst]


class InterviewState(MessagesState):
    max_num_turns: int
    context: Annotated[list, operator.add]
    analyst: Analyst
    interview: str
    sections: list


class SearchQuery(BaseModel):
    search_query: str = Field(None, description="Search query for retrieval.")


class InterviewBuilder:
    
    def __init__(self, llm):
        self.llm = llm
        self.analyst_instructions = analyst_instructions
        self.question_instructions = question_instructions
        self.answer_instructions = answer_instructions
        self.section_writer_instructions = section_writer_instructions
        self.tavily_search = TavilySearchResults(max_results=3)
        self.search_instructions = search_instructions

    def create_analysts(self, state: GenerateAnalystsState):

        """ Create analysts """
        topic=state['topic']
        max_analysts=state['max_analysts']
        human_analyst_feedback=state.get('human_analyst_feedback', '')
        structured_llm = self.llm.with_structured_output(Perspectives)
        system_message = analyst_instructions.format(topic=topic,
                                                    human_analyst_feedback=human_analyst_feedback,
                                                    max_analysts=max_analysts)
        analysts = structured_llm.invoke([SystemMessage(content=system_message)]+[HumanMessage(content="Generate the set of analysts.")])
        return {"analysts": analysts.analysts}


    def human_feedback(self, state: GenerateAnalystsState):
        """ No-op node that should be interrupted on """
        pass


    def should_continue(self, state: GenerateAnalystsState):
        """ Return the next node to execute """

        human_analyst_feedback=state.get('human_analyst_feedback', None)
        if human_analyst_feedback:
            return "create_analysts"
        return END
    

    def generate_question(self, state: InterviewState):
        """ Node to generate a question """
        analyst = state["analyst"]
        messages = state["messages"]
        system_message = self.question_instructions.format(goals=analyst.persona)
        question = self.llm.invoke([SystemMessage(content=system_message)]+messages)
        return {"messages": [question]}
    

    def search_web(self, state: InterviewState):

        """ Retrieve docs from web search """
        structured_llm = self.llm.with_structured_output(SearchQuery)
        search_query = structured_llm.invoke([self.search_instructions]+state['messages'])
        search_docs = self.tavily_search.invoke(search_query.search_query)
        formatted_search_docs = "\n\n---\n\n".join(
            [
                f'<Document href="{doc["url"]}"/>\n{doc["content"]}\n</Document>'
                for doc in search_docs
            ]
        )

        return {"context": [formatted_search_docs]}


    def search_wikipedia(self, state: InterviewState):

        """ Retrieve docs from wikipedia """
        structured_llm = self.llm.with_structured_output(SearchQuery)
        search_query = structured_llm.invoke([self.search_instructions]+state['messages'])
        search_docs = WikipediaLoader(query=search_query.search_query,
                                    load_max_docs=2).load()
        formatted_search_docs = "\n\n---\n\n".join(
            [
                f'<Document source="{doc.metadata["source"]}" page="{doc.metadata.get("page", "")}"/>\n{doc.page_content}\n</Document>'
                for doc in search_docs
            ]
        )
        return {"context": [formatted_search_docs]}
    

    def generate_answer(self, state: InterviewState):

        """ Node to answer a question """
        analyst = state["analyst"]
        messages = state["messages"]
        context = state["context"]
        system_message = self.answer_instructions.format(goals=analyst.persona, context=context)
        answer = self.llm.invoke([SystemMessage(content=system_message)]+messages)
        answer.name = "expert"
        return {"messages": [answer]}


    def save_interview(self, state: InterviewState):

        """ Save interviews """
        messages = state["messages"]
        interview = get_buffer_string(messages)
        return {"interview": interview}


    def route_messages(self, state: InterviewState,
                    name: str = "expert"):

        """ Route between question and answer """
        messages = state["messages"]
        max_num_turns = state.get('max_num_turns',2)
        num_responses = len(
            [m for m in messages if isinstance(m, AIMessage) and m.name == name]
        )
        if num_responses >= max_num_turns:
            return 'save_interview'
        last_question = messages[-2]

        if "Thank you so much for your help" in last_question.content:
            return 'save_interview'
        return "ask_question"
    

    def write_section(self, state: InterviewState):

        """ Node to answer a question """
        interview = state["interview"]
        context = state["context"]
        analyst = state["analyst"]
        system_message = self.section_writer_instructions.format(focus=analyst.description)
        section = self.llm.invoke([SystemMessage(content=system_message)]+[HumanMessage(content=f"Use this source to write your section: {context}")])
        return {"sections": [section.content]}
    

    def build(self):
        interview_builder = StateGraph(InterviewState)
        interview_builder.add_node("ask_question", self.generate_question)
        interview_builder.add_node("search_web", self.search_web)
        interview_builder.add_node("search_wikipedia", self.search_wikipedia)
        interview_builder.add_node("answer_question", self.generate_answer)
        interview_builder.add_node("save_interview", self.save_interview)
        interview_builder.add_node("write_section", self.write_section)

        interview_builder.add_edge(START, "ask_question")
        interview_builder.add_edge("ask_question", "search_web")
        interview_builder.add_edge("ask_question", "search_wikipedia")
        interview_builder.add_edge("search_web", "answer_question")
        interview_builder.add_edge("search_wikipedia", "answer_question")
        interview_builder.add_conditional_edges("answer_question", self.route_messages,['ask_question','save_interview'])
        interview_builder.add_edge("save_interview", "write_section")
        interview_builder.add_edge("write_section", END)

        return interview_builder
    
