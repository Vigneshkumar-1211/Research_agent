import os
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI 
from interview import Analyst, InterviewBuilder

import operator
from typing import List, Annotated
from typing_extensions import TypedDict

from langgraph.constants import Send
from prompts import report_writer_instructions, intro_conclusion_instructions
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()
def get_llm():
    llm = ChatOpenAI(
        model=os.environ["MODEL"], 
        temperature=0.7,
        openai_api_key=os.environ["NOVITA_API_KEY"],
        openai_api_base=os.environ["OPENAI_BASE"],  
    )

    return llm 

class ResearchGraphState(TypedDict):
    topic: str
    max_analysts: int
    human_analyst_feedback: str
    analysts: List[Analyst] 
    sections: Annotated[list, operator.add]
    introduction: str
    content: str
    conclusion: str
    final_report: str

class ResearchAgent:
    def __init__(self, templatePrompt):
        self.llm = get_llm()
        self.templatePrompt = templatePrompt
        self.report_writer_instructions = report_writer_instructions
        print("\n\n\n\n")
        print(self.report_writer_instructions)
        print("\n\n\n\n")
        self.intro_conclusion_instructions = intro_conclusion_instructions
        self.interview_builder = InterviewBuilder(self.llm)


    def initiate_all_interviews(self, state: ResearchGraphState):
        """ This is the "map" step where we run each interview sub-graph using Send API """

        human_analyst_feedback=state.get('human_analyst_feedback')
        if human_analyst_feedback:

            return "create_analysts"

        else:
            topic = state["topic"]
            return [Send("conduct_interview", {"analyst": analyst,
                                            "messages": [HumanMessage(
                                                content=f"So you said you were writing an article on {topic}?"
                                            )
                                                        ]}) for analyst in state["analysts"]]


    def write_report(self, state: ResearchGraphState):

        sections = state["sections"]
        topic = state["topic"]

        formatted_str_sections = "\n\n".join([f"{section}" for section in sections])
        system_message = self.report_writer_instructions.format(topic=topic, context=formatted_str_sections, template=self.templatePrompt)
        report = self.llm.invoke([SystemMessage(content=system_message)]+[HumanMessage(content=f"Write a report based upon these memos.")])
        return {"content": report.content}
    

    def write_introduction(self, state: ResearchGraphState):

        sections = state["sections"]
        topic = state["topic"]

        formatted_str_sections = "\n\n".join([f"{section}" for section in sections])

        instructions = intro_conclusion_instructions.format(topic=topic, formatted_str_sections=formatted_str_sections)
        intro = self.llm.invoke([instructions]+[HumanMessage(content=f"Write the report introduction")])
        return {"introduction": intro.content}


    def write_conclusion(self, state: ResearchGraphState):

        sections = state["sections"]
        topic = state["topic"]

        formatted_str_sections = "\n\n".join([f"{section}" for section in sections])


        instructions = intro_conclusion_instructions.format(topic=topic, formatted_str_sections=formatted_str_sections)
        conclusion = self.llm.invoke([instructions]+[HumanMessage(content=f"Write the report conclusion")])
        return {"conclusion": conclusion.content}


    def finalize_report(self, state: ResearchGraphState):
        """ The is the "reduce" step where we gather all the sections, combine them, and reflect on them to write the intro/conclusion """

        content = state["content"]
        if content.startswith("## Insights"):
            content = content
            
        if "## Sources" in content:
            try:
                content, sources = content.split("\n## Sources\n")
            except:
                sources = None
        else:
            sources = None

        final_report = state["introduction"] + "\n\n---\n\n" + content + "\n\n---\n\n" + state["conclusion"]
        if sources is not None:
            final_report += "\n\n## Sources\n" + sources
        return {"final_report": final_report}
    

    def build(self):
        builder = StateGraph(ResearchGraphState)
        builder.add_node("create_analysts", self.interview_builder.create_analysts)
        builder.add_node("human_feedback",  self.interview_builder.human_feedback)
        builder.add_node("conduct_interview", self.interview_builder.build().compile())
        builder.add_node("write_report",self.write_report)
        builder.add_node("write_introduction",self.write_introduction)
        builder.add_node("write_conclusion",self.write_conclusion)
        builder.add_node("finalize_report",self.finalize_report)

        builder.add_edge(START, "create_analysts")
        builder.add_edge("create_analysts", "human_feedback")
        builder.add_conditional_edges("human_feedback", self.initiate_all_interviews, ["create_analysts", "conduct_interview"])
        builder.add_edge("conduct_interview", "write_report")
        builder.add_edge("conduct_interview", "write_introduction")
        builder.add_edge("conduct_interview", "write_conclusion")
        builder.add_edge(["write_conclusion", "write_report", "write_introduction"], "finalize_report")
        builder.add_edge("finalize_report", END)

        memory = MemorySaver()
        graph = builder.compile(interrupt_before=['human_feedback'], checkpointer=memory)
        return graph