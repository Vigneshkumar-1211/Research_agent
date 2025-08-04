from fastapi import FastAPI
from pydantic import BaseModel
from researchAgent import ResearchAgent
from typing import List
from typing import List, Dict, Any
import uuid
from fastapi.middleware.cors import CORSMiddleware
from prompts import template
from doc_generator import Generator
from fastapi.responses import FileResponse


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

active_agent_graphs: Dict[str, Any] = {}

class Data(BaseModel):
    max_analysts: int
    topic: str
    templatePrompt  : str | None

class HumanFeedback(BaseModel):
    human_feedback: str|None
    thread_id: str

class DocRequest(BaseModel):
    format: str
    content: str


doc_gen = Generator()

report = {
    "pdf": doc_gen.generate_pdf,
    "doc": doc_gen.generate_doc,
    "ppt": doc_gen.generate_pptx
}

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/start_research")
async def start_research(data: Data):
    
    thread_id = str(uuid.uuid4())
    thread = {"configurable": {"thread_id": thread_id}}

    if data.templatePrompt:
        instructions = data.templatePrompt
    else:
        instructions = template
    agentGraph = ResearchAgent(instructions).build()
    result = agentGraph.invoke({
        "topic":data.topic,
        "max_analysts":data.max_analysts,
    },
    thread
    )

    print("\n\n Result: ", result)
    print("\n\n")
    print(agentGraph.get_state(thread).next)
    print("\n\n")

    analysts = result.get('analysts', '')
    if analysts:
        for analyst in analysts:
            print(f"Name: {analyst.name}")
            print(f"Affiliation: {analyst.affiliation}")
            print(f"Role: {analyst.role}")
            print(f"Description: {analyst.description}")
            print("-" * 50)

    active_agent_graphs[thread_id] = agentGraph
    return {
        "analysts": analysts,
        "thread_id": thread_id  
    }

@app.post("/provide_feedback")
async def provide_feedback(human_feedback: HumanFeedback):
    thread_id = human_feedback.thread_id
    if thread_id not in active_agent_graphs:
        return {"error": "Invalid thread_id or research session expired"}
    
    agentGraph = active_agent_graphs[thread_id]
    thread = {"configurable": {"thread_id": thread_id}}
    state = agentGraph.get_state(thread)

    if state.next and state.next[0] == "human_feedback":
        agentGraph.update_state(
            thread, 
            {"human_analyst_feedback": human_feedback.human_feedback}, 
            as_node="human_feedback"
        )

        result = agentGraph.invoke(None, thread)
        print("/n/nProvide Feedback:", result)
        print("\n\n")
        return result
        
    elif state.next and state.next[0] is None:
        agentGraph.update_state(
            thread,
            {"human_analyst_feedback": None},
            as_node="human_feedback"
        )
        result = agentGraph.invoke(None, thread)
        report = result.get('final_report', '')
        print("/n/nProvide Feedback:", result)
        print("\n\n")
        return report
    
    return {"status": "Feedback not processed - unexpected state"}

@app.post("/generate_doc")
async def generate_doc(request: DocRequest):
    generate = report[request.format]
    filepath = generate(request.content)
    filename = filepath.split('/')[1]
    return FileResponse(filepath, filename=filename, media_type='application/octet-stream')


    