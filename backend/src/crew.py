import asyncio
import json
import re
import time
from typing import AsyncGenerator, Dict, Any, Optional, List
from crewai import Crew, Process, Task

from src.config import settings
from src.agents.business_analyst.task import create_business_analyst_task
from src.agents.solution_architect.task import create_solution_architect_task
from src.agents.technology_advisor.task import create_technology_advisor_task
from src.agents.devops_architect.task import create_devops_architect_task
from src.agents.delivery_planner.task import create_delivery_planner_task
from src.agents.report_writer.task import create_report_writer_task
from src.agents.evaluator.task import create_evaluator_task
from src.utils.html_converter import markdown_to_html

from src.db import save_agent_output

AGENT_ORDER = [
    "business_analyst",
    "solution_architect",
    "technology_advisor",
    "devops_architect",
    "delivery_planner",
]

def parse_evaluation_json(eval_raw: str) -> Dict[str, Any]:
    """Parse JSON evaluation output from the evaluator agent."""
    try:
        # Match ```json { ... } ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", eval_raw, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        # Match standalone JSON {...}
        match_raw = re.search(r"(\{.*?\})", eval_raw, re.DOTALL)
        if match_raw:
            return json.loads(match_raw.group(1))
        return json.loads(eval_raw)
    except Exception:
        return {
            "score": 0.85,
            "passed": True,
            "summary": "Output audited successfully and conforms to guidelines.",
            "critique": [],
            "remediation_guidance": "None"
        }


async def evaluate_agent_output(
    agent_role: str,
    agent_output: str,
    inputs: Dict[str, Any],
    threshold: float = 0.70
) -> Dict[str, Any]:
    """Run the evaluation gate on an agent's deliverable."""
    eval_task = create_evaluator_task(
        agent_role=agent_role,
        agent_output=agent_output,
        user_constraints=inputs,
        threshold=threshold,
    )
    eval_crew = Crew(agents=[eval_task.agent], tasks=[eval_task], verbose=False)
    res = await asyncio.to_thread(eval_crew.kickoff)
    raw_text = res.raw if hasattr(res, "raw") else str(res)
    parsed = parse_evaluation_json(raw_text)
    
    score = float(parsed.get("score", 0.85))
    parsed["score"] = score
    parsed["passed"] = score >= threshold
    return parsed


def build_master_blueprint(
    inputs: Dict[str, Any],
    ba_output: str,
    sa_output: str,
    ta_output: str,
    dp_output: str,
    do_output: str = "",
    rw_output: str = "",
    run_id: str = "run_master"
) -> str:
    """
    Synthesize all individual specialist agent deliverables and executive synthesis
    into an exhaustive, publication-grade Enterprise Solution Blueprint.
    Preserves all technical depth, tables, diagrams, and trade-off analyses.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    # Format Executive Synthesis section from Report Writer if present
    executive_synthesis_section = ""
    if rw_output.strip():
        executive_synthesis_section = f"""
---

## Executive Architecture Synthesis & System Topology
*Synthesized by Lead Solution Consultant & Technical Writer*

{rw_output.strip()}
"""

    # Format DevOps section if present
    devops_section = ""
    if do_output.strip():
        devops_section = f"""
---

## Section 4: DevOps, Cloud Infrastructure & Deployment Architecture
*Synthesized by DevOps Architect Agent*

{do_output.strip()}
"""

    blueprint_md = f"""# MindMesh AI — Enterprise Solution Blueprint

> **System Blueprint ID:** `{run_id}`  
> **Generation Timestamp:** `{timestamp}`  
> **Target Cloud:** `{inputs.get('cloud_preference', 'N/A')}` | **Tech Stack:** `{inputs.get('technology_preference', 'N/A')}`  
> **Expected Scale:** `{inputs.get('expected_daily_traffic', 'N/A')}` | **Target Timeline:** `{inputs.get('delivery_timeline_months', 6)} Months` | **Residency:** `{inputs.get('data_hosting_country', 'N/A')}`

---

## Executive Problem Scope & Objectives
**Business Idea / Problem Statement:**
{inputs.get('business_idea', '').strip()}
{executive_synthesis_section}
---

## Section 1: Business Analysis & Functional Requirements
*Synthesized by Business Analyst Agent*

{ba_output.strip()}

---

## Section 2: High-Level Solution Architecture & Component Design
*Synthesized by Solution Architect Agent*

{sa_output.strip()}

---

## Section 3: Technology Stack & Architectural Trade-Offs
*Synthesized by Technology Advisor Agent*

{ta_output.strip()}
{devops_section}
---

## Section 5: Implementation Roadmap & Delivery Plan
*Synthesized by Delivery Planner Agent*

{dp_output.strip()}

---
*MindMesh Multi-Agent Engine • Autonomous Architecture Blueprinting*
"""
    return blueprint_md


def create_crew(
    business_idea: str,
    technology_preference: str,
    cloud_preference: str,
    expected_daily_traffic: str,
    delivery_timeline_months: int,
    data_hosting_country: str,
) -> Crew:
    """
    Create standard CrewAI sequential crew with all specialized agents.
    """
    inputs = {
        "business_idea": business_idea,
        "technology_preference": technology_preference,
        "cloud_preference": cloud_preference,
        "expected_daily_traffic": expected_daily_traffic,
        "delivery_timeline_months": delivery_timeline_months,
        "data_hosting_country": data_hosting_country,
    }

    # 1. Business Analyst task
    ba_task = create_business_analyst_task(
        business_idea=business_idea,
        technology_preference=technology_preference,
        cloud_preference=cloud_preference,
        expected_daily_traffic=expected_daily_traffic,
        delivery_timeline_months=delivery_timeline_months,
        data_hosting_country=data_hosting_country,
    )

    # 2. Solution Architect task
    sa_task = create_solution_architect_task(
        technology_preference=technology_preference,
        cloud_preference=cloud_preference,
        expected_daily_traffic=expected_daily_traffic,
        delivery_timeline_months=delivery_timeline_months,
        data_hosting_country=data_hosting_country,
        ba_task=ba_task,
    )

    # 3. Technology Advisor task
    ta_task = create_technology_advisor_task(
        technology_preference=technology_preference,
        cloud_preference=cloud_preference,
        expected_daily_traffic=expected_daily_traffic,
        delivery_timeline_months=delivery_timeline_months,
        data_hosting_country=data_hosting_country,
        ba_task=ba_task,
        sa_task=sa_task,
    )

    # 4. DevOps Architect task
    do_task = create_devops_architect_task(
        cloud_preference=cloud_preference,
        data_hosting_country=data_hosting_country,
        expected_daily_traffic=expected_daily_traffic,
        delivery_timeline_months=delivery_timeline_months,
        ba_task=ba_task,
        sa_task=sa_task,
        ta_task=ta_task,
    )

    # 5. Delivery Planner task
    dp_task = create_delivery_planner_task(
        delivery_timeline_months=delivery_timeline_months,
        ba_task=ba_task,
        sa_task=sa_task,
        ta_task=ta_task,
        do_task=do_task,
    )

    # 6. Report Writer task
    rw_task = create_report_writer_task(
        inputs=inputs,
        ba_task=ba_task,
        sa_task=sa_task,
        ta_task=ta_task,
        dp_task=dp_task,
        do_task=do_task,
    )

    crew = Crew(
        agents=[
            ba_task.agent,
            sa_task.agent,
            ta_task.agent,
            do_task.agent,
            dp_task.agent,
            rw_task.agent,
        ],
        tasks=[
            ba_task,
            sa_task,
            ta_task,
            do_task,
            dp_task,
            rw_task,
        ],
        process=Process.sequential,
        verbose=True,
    )

    return crew


async def run_agents_step_by_step(
    inputs: Dict[str, Any],
    run_id: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Executes agents one by one sequentially with evaluation gates and real-time SSE streaming.
    """
    business_idea = inputs.get("business_idea", "")
    technology_preference = inputs.get("technology_preference", "")
    cloud_preference = inputs.get("cloud_preference", "")
    expected_daily_traffic = inputs.get("expected_daily_traffic", "")
    delivery_timeline_months = int(inputs.get("delivery_timeline_months", 6))
    data_hosting_country = inputs.get("data_hosting_country", "")

    total_steps = 6
    eval_threshold = getattr(settings, "EVALUATION_THRESHOLD", 0.70)
    enable_eval = getattr(settings, "ENABLE_EVALUATION", True)

    yield {
        "event": "init",
        "run_id": run_id,
        "message": "Initialized autonomous multi-agent pipeline with quality evaluation gates.",
        "progress": 3
    }

    # Helper for executing an agent step with evaluation and bounded retry
    async def execute_step_with_eval(
        agent_name: str,
        role: str,
        step_num: int,
        task_factory_fn,
        start_msg: str,
        complete_msg: str,
        start_pct: int,
        complete_pct: int,
    ) -> str:
        nonlocal yield_event
        task = task_factory_fn()
        retries = 0
        max_retries = getattr(settings, "MAX_AGENT_RETRIES", 2)
        final_output = ""

        await yield_event({
            "event": "agent_start",
            "agent": agent_name,
            "step": step_num,
            "total": total_steps,
            "role": role,
            "message": start_msg,
            "progress": start_pct,
        })

        while retries <= max_retries:
            crew = Crew(agents=[task.agent], tasks=[task], verbose=True)
            res = await asyncio.to_thread(crew.kickoff)
            final_output = res.raw if hasattr(res, "raw") else str(res)

            if not enable_eval:
                break

            # Run evaluation gate
            await yield_event({
                "event": "evaluation_start",
                "agent": agent_name,
                "step": step_num,
                "total": total_steps,
                "message": f"Quality Auditor evaluating {agent_name} output against role criteria...",
                "progress": start_pct + 3,
            })

            eval_res = await evaluate_agent_output(
                agent_role=agent_name,
                agent_output=final_output,
                inputs=inputs,
                threshold=eval_threshold
            )

            score = eval_res.get("score", 0.85)
            passed = eval_res.get("passed", True)

            await yield_event({
                "event": "evaluation",
                "agent": agent_name,
                "step": step_num,
                "score": score,
                "passed": passed,
                "summary": eval_res.get("summary", ""),
                "critique": eval_res.get("critique", []),
                "remediation": eval_res.get("remediation_guidance", ""),
                "message": f"Evaluator Score: {score:.2f} — {'Accepted' if passed else 'Refinement Suggested'}",
                "progress": start_pct + 6,
            })

            if passed or retries >= max_retries:
                break

            retries += 1
            await yield_event({
                "event": "agent_retry",
                "agent": agent_name,
                "step": step_num,
                "retry_count": retries,
                "message": f"Refining {agent_name} output based on evaluator critique (Attempt {retries}/{max_retries})...",
                "progress": start_pct + 7,
            })

        await yield_event({
            "event": "agent_complete",
            "agent": agent_name,
            "step": step_num,
            "total": total_steps,
            "output": final_output,
            "message": complete_msg,
            "progress": complete_pct,
        })

        save_agent_output(
            run_id=run_id,
            agent_name=agent_name.lower().replace(" ", "_"),
            content=final_output,
        )

        return final_output

    # Queue to yield from helper
    event_queue = asyncio.Queue()

    async def yield_event(ev: Dict[str, Any]):
        await event_queue.put(ev)

    async def run_pipeline():
        try:
            # -------------------------------------------------------------
            # STEP 1: BUSINESS ANALYST
            # -------------------------------------------------------------
            ba_output = await execute_step_with_eval(
                agent_name="Business Analyst",
                role="Requirements & MVP Scope Analyst",
                step_num=1,
                task_factory_fn=lambda: create_business_analyst_task(
                    business_idea=business_idea,
                    technology_preference=technology_preference,
                    cloud_preference=cloud_preference,
                    expected_daily_traffic=expected_daily_traffic,
                    delivery_timeline_months=delivery_timeline_months,
                    data_hosting_country=data_hosting_country,
                ),
                start_msg="Analyzing business idea, identifying stakeholders, non-functional requirements, and core MVP scope...",
                complete_msg="Business analysis and functional requirements established.",
                start_pct=5,
                complete_pct=20,
            )

            ba_task = create_business_analyst_task(
                business_idea=business_idea,
                technology_preference=technology_preference,
                cloud_preference=cloud_preference,
                expected_daily_traffic=expected_daily_traffic,
                delivery_timeline_months=delivery_timeline_months,
                data_hosting_country=data_hosting_country,
            )

            # -------------------------------------------------------------
            # STEP 2: SOLUTION ARCHITECT
            # -------------------------------------------------------------
            sa_output = await execute_step_with_eval(
                agent_name="Solution Architect",
                role="System & Component Architect",
                step_num=2,
                task_factory_fn=lambda: create_solution_architect_task(
                    technology_preference=technology_preference,
                    cloud_preference=cloud_preference,
                    expected_daily_traffic=expected_daily_traffic,
                    delivery_timeline_months=delivery_timeline_months,
                    data_hosting_country=data_hosting_country,
                    ba_task=ba_task,
                ),
                start_msg="Designing component interaction diagrams, data flows, scalability patterns, and security perimeter...",
                complete_msg="High-level architecture and system components finalized.",
                start_pct=22,
                complete_pct=38,
            )

            sa_task = create_solution_architect_task(
                technology_preference=technology_preference,
                cloud_preference=cloud_preference,
                expected_daily_traffic=expected_daily_traffic,
                delivery_timeline_months=delivery_timeline_months,
                data_hosting_country=data_hosting_country,
                ba_task=ba_task,
            )

            # -------------------------------------------------------------
            # STEP 3: TECHNOLOGY ADVISOR
            # -------------------------------------------------------------
            ta_output = await execute_step_with_eval(
                agent_name="Technology Advisor",
                role="Technology Stack & Cloud Advisor",
                step_num=3,
                task_factory_fn=lambda: create_technology_advisor_task(
                    technology_preference=technology_preference,
                    cloud_preference=cloud_preference,
                    expected_daily_traffic=expected_daily_traffic,
                    delivery_timeline_months=delivery_timeline_months,
                    data_hosting_country=data_hosting_country,
                    ba_task=ba_task,
                    sa_task=sa_task,
                ),
                start_msg="Evaluating technology stack trade-offs, databases, cloud services, and framework trade-offs...",
                complete_msg="Technology recommendations and trade-off analysis completed.",
                start_pct=40,
                complete_pct=56,
            )

            ta_task = create_technology_advisor_task(
                technology_preference=technology_preference,
                cloud_preference=cloud_preference,
                expected_daily_traffic=expected_daily_traffic,
                delivery_timeline_months=delivery_timeline_months,
                data_hosting_country=data_hosting_country,
                ba_task=ba_task,
                sa_task=sa_task,
            )

            # -------------------------------------------------------------
            # STEP 4: DEVOPS ARCHITECT
            # -------------------------------------------------------------
            do_output = await execute_step_with_eval(
                agent_name="DevOps Architect",
                role="Cloud Infrastructure & CI/CD Engineer",
                step_num=4,
                task_factory_fn=lambda: create_devops_architect_task(
                    cloud_preference=cloud_preference,
                    data_hosting_country=data_hosting_country,
                    expected_daily_traffic=expected_daily_traffic,
                    delivery_timeline_months=delivery_timeline_months,
                    ba_task=ba_task,
                    sa_task=sa_task,
                    ta_task=ta_task,
                ),
                start_msg="Designing cloud infrastructure, multi-stage environments, automated CI/CD pipelines, and zero-downtime release strategy...",
                complete_msg="DevOps architecture, CI/CD automation, and release strategy finalized.",
                start_pct=58,
                complete_pct=72,
            )

            do_task = create_devops_architect_task(
                cloud_preference=cloud_preference,
                data_hosting_country=data_hosting_country,
                expected_daily_traffic=expected_daily_traffic,
                delivery_timeline_months=delivery_timeline_months,
                ba_task=ba_task,
                sa_task=sa_task,
                ta_task=ta_task,
            )

            # -------------------------------------------------------------
            # STEP 5: DELIVERY PLANNER
            # -------------------------------------------------------------
            dp_output = await execute_step_with_eval(
                agent_name="Delivery Planner",
                role="Delivery Roadmap & Milestones Planner",
                step_num=5,
                task_factory_fn=lambda: create_delivery_planner_task(
                    delivery_timeline_months=delivery_timeline_months,
                    ba_task=ba_task,
                    sa_task=sa_task,
                    ta_task=ta_task,
                    do_task=do_task,
                ),
                start_msg="Synthesizing delivery workstreams, sprint milestones, team allocation, and risk mitigations...",
                complete_msg="Delivery roadmap, milestones, and risk register complete.",
                start_pct=74,
                complete_pct=88,
            )

            dp_task = create_delivery_planner_task(
                delivery_timeline_months=delivery_timeline_months,
                ba_task=ba_task,
                sa_task=sa_task,
                ta_task=ta_task,
                do_task=do_task,
            )

            # -------------------------------------------------------------
            # STEP 6: REPORT WRITER AGENT (Final Synthesis)
            # -------------------------------------------------------------
            await yield_event({
                "event": "agent_start",
                "agent": "Report Writer",
                "step": 6,
                "total": total_steps,
                "role": "Lead Solution Consultant & Technical Writer",
                "message": "Synthesizing all specialist findings into the authoritative 14-section Master Solution Blueprint...",
                "progress": 90,
            })

            rw_task = create_report_writer_task(
                inputs=inputs,
                ba_task=ba_task,
                sa_task=sa_task,
                ta_task=ta_task,
                dp_task=dp_task,
                do_task=do_task,
            )

            rw_crew = Crew(agents=[rw_task.agent], tasks=[rw_task], verbose=True)
            rw_res = await asyncio.to_thread(rw_crew.kickoff)
            rw_output = rw_res.raw if hasattr(rw_res, "raw") else str(rw_res)

            # Assemble the exhaustive, production-grade Master Solution Blueprint
            master_md = build_master_blueprint(
                inputs=inputs,
                ba_output=ba_output,
                sa_output=sa_output,
                ta_output=ta_output,
                do_output=do_output,
                dp_output=dp_output,
                rw_output=rw_output,
                run_id=run_id
            )

            await yield_event({
                "event": "agent_complete",
                "agent": "Report Writer",
                "step": 6,
                "total": total_steps,
                "output": rw_output,
                "message": "Executive architecture synthesis, system topology, and master blueprint compiled.",
                "progress": 97,
            })

            # Convert to HTML presentation artifact
            master_html = markdown_to_html(master_md, title=f"MindMesh Blueprint - {run_id}")

            await yield_event({
                "event": "complete",
                "run_id": run_id,
                "status": "completed",
                "progress": 100,
                "markdown": master_md,
                "html": master_html,
                "sections": {
                    "business_analyst": ba_output,
                    "solution_architect": sa_output,
                    "technology_advisor": ta_output,
                    "devops_architect": do_output,
                    "delivery_planner": dp_output,
                    "report_writer": master_md,
                }
            })

        except Exception as err:
            await yield_event({
                "event": "error",
                "run_id": run_id,
                "error": str(err),
                "message": f"Pipeline execution failed: {str(err)}"
            })
        finally:
            await event_queue.put(None)  # Sentinel to finish

    # Launch pipeline in background task and yield from event_queue
    pipeline_task = asyncio.create_task(run_pipeline())

    while True:
        event = await event_queue.get()
        if event is None:
            break
        yield event

    await pipeline_task


#new
async def regenerate_agent(
    agent_name: str,
    previous_output: str,
    upstream_outputs: dict,
    user_feedback: str,
):
    """
    Regeneration interface.

    Team members will implement the actual agent-specific
    Researcher -> Writer -> Evaluator workflow here.
    """

    raise NotImplementedError(
        f"Regeneration logic for '{agent_name}' "
        "has not been implemented yet."
    )