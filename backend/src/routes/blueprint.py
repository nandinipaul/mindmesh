import json
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.crew import create_crew, run_agents_step_by_step, build_master_blueprint, AGENT_ORDER, regenerate_agent
from src.utils.output_file import save_output
from src.utils.html_converter import markdown_to_html
from src.db import (
    save_blueprint_record,
    get_blueprint_history,
    get_blueprint_by_run_id,
    delete_blueprint_by_run_id,
    get_current_agent_outputs,
    get_agent_output,
    save_agent_output,
)


router = APIRouter(prefix="/blueprints", tags=["Blueprints"])


class BlueprintRequest(BaseModel):
    business_idea: str
    technology_preference: str 
    cloud_preference: str 
    expected_daily_traffic: str 
    delivery_timeline_months: int
    data_hosting_country: str 

class RegenerateRequest(BaseModel):
    agent: str
    feedback: str


@router.get("", status_code=200)
@router.get("/list", status_code=200)
@router.get("/history", status_code=200)
async def list_blueprints():
    """List all saved blueprints and history from SQLite database."""
    history = get_blueprint_history(limit=100)
    run_ids = [item["run_id"] for item in history]

    # Fallback to filesystem if DB was empty but files exist
    if not run_ids:
        outputs_dir = Path("outputs")
        if outputs_dir.exists():
            run_ids = [
                file.stem for file in outputs_dir.glob("*.html") if file.stem != "final_output"
            ]
            run_ids.sort(reverse=True)

    return {
        "total": len(history) if history else len(run_ids),
        "run_ids": run_ids,
        "history": history
    }


@router.post("/stream")
async def stream_blueprint_execution(payload: BlueprintRequest):
    """
    Stream agent execution step-by-step using Server-Sent Events (SSE) and save to SQLite.
    """
    run_id = str(uuid.uuid4())[:12]
    payload_dict = payload.model_dump()

    async def sse_event_stream():
        try:
            async for event_data in run_agents_step_by_step(payload_dict, run_id=run_id):
                # When complete event is produced, persist to SQLite DB and files
                if event_data.get("event") == "complete":
                    html_content = event_data.get("html", "")
                    md_content = event_data.get("markdown", "")
                    
                    # Persist to SQLite DB
                    try:
                        save_blueprint_record(
                            run_id=run_id,
                            inputs=payload_dict,
                            markdown_content=md_content,
                            html_content=html_content,
                            status="completed"
                        )
                    except Exception as db_err:
                        print(f"Database save warning: {db_err}")

                    # Also save fallback file outputs
                    save_output(f"{run_id}.html", html_content)
                    save_output(f"{run_id}.md", md_content)
                    save_output("final_output.html", html_content)
                    save_output("final_output.md", md_content)
                    event_data["file_saved"] = f"outputs/{run_id}.html"

                yield f"data: {json.dumps(event_data)}\n\n"

        except Exception as e:
            err_event = {
                "event": "error",
                "run_id": run_id,
                "error": str(e),
                "message": f"Execution failed: {str(e)}"
            }
            yield f"data: {json.dumps(err_event)}\n\n"

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("", status_code=201)
@router.post("/generate", status_code=201)
async def create_blueprint(payload: BlueprintRequest):
    """
    Synchronous / standard generation endpoint saving to SQLite DB.
    """
    run_id = str(uuid.uuid4())[:12]
    payload_dict = payload.model_dump()

    try:
        crew = create_crew(**payload_dict)
        result = await crew.kickoff_async()
        #if hasattr(result, "tasks_output") and len(result.tasks_output) >= 6:   recommended change
        if hasattr(result, "tasks_output") and len(result.tasks_output) >= 5:
            ba_out = result.tasks_output[0].raw
            sa_out = result.tasks_output[1].raw
            ta_out = result.tasks_output[2].raw
            do_out = result.tasks_output[3].raw
            dp_out = result.tasks_output[4].raw
            rw_out = result.tasks_output[5].raw if len(result.tasks_output) > 5 else ""
            final_output = build_master_blueprint(
                inputs=payload_dict,
                ba_output=ba_out,
                sa_output=sa_out,
                ta_output=ta_out,
                do_output=do_out,
                dp_output=dp_out,
                rw_output=rw_out,
                run_id=run_id
            )
        else:
            final_output = result.raw if hasattr(result, "raw") else str(result)
        
        html_content = markdown_to_html(final_output, title=f"MindMesh Blueprint - {run_id}")
        
        # Save to SQLite Database
        save_blueprint_record(
            run_id=run_id,
            inputs=payload_dict,
            markdown_content=final_output,
            html_content=html_content,
            status="completed"
        )

        save_output(f"{run_id}.html", html_content)
        save_output(f"{run_id}.md", final_output)
        save_output("final_output.html", html_content)
        save_output("final_output.md", final_output)

        return {
            "run_id": run_id,
            "status": "completed",
            "file_saved": f"outputs/{run_id}.html",
            "markdown": final_output,
            "result": html_content
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Blueprint generation failed: {str(e)}"
        )


@router.get("/{run_id}", status_code=200)
async def get_blueprint(run_id: str):
    """Retrieve saved blueprint output from SQLite database or fallback files."""
    # First attempt from SQLite DB
    db_record = get_blueprint_by_run_id(run_id)
    if db_record:
        return {
            "run_id": run_id,
            "status": db_record.get("status", "completed"),
            "result": db_record.get("html_content", ""),
            "markdown": db_record.get("markdown_content", ""),
            "html": db_record.get("html_content", ""),
            "created_at": db_record.get("created_at", ""),
            "business_idea": db_record.get("business_idea", ""),
            "technology_preference": db_record.get("technology_preference", ""),
            "cloud_preference": db_record.get("cloud_preference", "")
        }

    # Fallback to filesystem
    outputs_dir = Path("outputs")
    html_file = outputs_dir / f"{run_id}.html"
    md_file = outputs_dir / f"{run_id}.md"

    if not html_file.exists() and not md_file.exists():
        raise HTTPException(
            status_code=404, 
            detail=f"Blueprint output for run_id '{run_id}' not found."
        )

    html_content = html_file.read_text(encoding="utf-8") if html_file.exists() else ""
    md_content = md_file.read_text(encoding="utf-8") if md_file.exists() else ""

    return {
        "run_id": run_id,
        "status": "completed",
        "result": html_content,
        "markdown": md_content,
        "html": html_content
    }

#new
@router.post("/{run_id}/regenerate")
async def regenerate_blueprint_agent(
    run_id: str,
    payload: RegenerateRequest
):
    """
    Regenerate the selected agent and cascade regeneration
    through all downstream specialist agents.

    Agents before the selected agent remain unchanged.
    """

    # ---------------------------------------------------------
    # 1. Validate agent
    # ---------------------------------------------------------
    if payload.agent not in AGENT_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent: {payload.agent}"
        )

    # ---------------------------------------------------------
    # 2. Check blueprint exists
    # ---------------------------------------------------------
    blueprint = get_blueprint_by_run_id(run_id)

    if not blueprint:
        raise HTTPException(
            status_code=404,
            detail=f"Blueprint '{run_id}' not found."
        )

    # ---------------------------------------------------------
    # 3. Get current output of selected agent
    # ---------------------------------------------------------
    selected_output = get_agent_output(
        run_id,
        payload.agent
    )

    if not selected_output:
        raise HTTPException(
            status_code=404,
            detail=f"No current output found for agent '{payload.agent}'."
        )

    # ---------------------------------------------------------
    # 4. Determine cascade range
    # ---------------------------------------------------------
    selected_index = AGENT_ORDER.index(payload.agent)

    agents_to_regenerate = AGENT_ORDER[selected_index:]

    regenerated_outputs = {}

    # ---------------------------------------------------------
    # 5. Regenerate selected agent + downstream agents
    # ---------------------------------------------------------
    for index, agent_name in enumerate(agents_to_regenerate):

        current_output = get_agent_output(
            run_id,
            agent_name
        )

        if not current_output:
            raise HTTPException(
                status_code=404,
                detail=f"No current output found for agent '{agent_name}'."
            )

        # Gather CURRENT outputs from all upstream agents
        upstream_outputs = {}

        agent_position = AGENT_ORDER.index(agent_name)

        for upstream_agent in AGENT_ORDER[:agent_position]:

            upstream_output = get_agent_output(
                run_id,
                upstream_agent
            )

            if upstream_output:
                upstream_outputs[upstream_agent] = (
                    upstream_output["content"]
                )

        # For the first agent, use the user's feedback.
        # For downstream agents, cascade automatically.
        feedback = payload.feedback if index == 0 else (
            f"This agent is being regenerated because "
            f"the upstream agent '{AGENT_ORDER[agent_position - 1]}' "
            f"was regenerated. Reconcile your output with the "
            f"latest upstream outputs."
        )

        regenerated_output = await regenerate_agent(
            agent_name=agent_name,
            previous_output=current_output["content"],
            upstream_outputs=upstream_outputs,
            user_feedback=feedback,
        )

        # Save as a NEW version.
        # save_agent_output automatically:
        # - increments version
        # - marks old version non-current
        # - marks new version current
        save_agent_output(
            run_id=run_id,
            agent_name=agent_name,
            content=regenerated_output,
            user_feedback=feedback,
        )

        regenerated_outputs[agent_name] = regenerated_output

    # ---------------------------------------------------------
    # 6. Load CURRENT outputs after entire cascade
    # ---------------------------------------------------------
    current_outputs = {}

    for agent_name in AGENT_ORDER:

        output = get_agent_output(
            run_id,
            agent_name
        )

        if output:
            current_outputs[agent_name] = output["content"]

    # ---------------------------------------------------------
    # 7. Rebuild Master Blueprint
    # ---------------------------------------------------------
    master_md = build_master_blueprint(
        inputs={
            "business_idea": blueprint.get("business_idea", ""),
            "technology_preference": blueprint.get(
                "technology_preference", ""
            ),
            "cloud_preference": blueprint.get(
                "cloud_preference", ""
            ),
            "expected_daily_traffic": blueprint.get(
                "expected_daily_traffic", ""
            ),
            "delivery_timeline_months": blueprint.get(
                "delivery_timeline_months", 6
            ),
            "data_hosting_country": blueprint.get(
                "data_hosting_country", ""
            ),
        },
        ba_output=current_outputs.get(
            "business_analyst", ""
        ),
        sa_output=current_outputs.get(
            "solution_architect", ""
        ),
        ta_output=current_outputs.get(
            "technology_advisor", ""
        ),
        do_output=current_outputs.get(
            "devops_architect", ""
        ),
        dp_output=current_outputs.get(
            "delivery_planner", ""
        ),
        rw_output="",
        run_id=run_id,
    )

    # ---------------------------------------------------------
    # 8. Convert master blueprint to HTML
    # ---------------------------------------------------------
    html_content = markdown_to_html(
        master_md,
        title=f"MindMesh Blueprint - {run_id}"
    )

    # ---------------------------------------------------------
    # 9. Update saved blueprint
    # ---------------------------------------------------------
    save_blueprint_record(
        run_id=run_id,
        inputs={
            "business_idea": blueprint.get(
                "business_idea", ""
            ),
            "technology_preference": blueprint.get(
                "technology_preference", ""
            ),
            "cloud_preference": blueprint.get(
                "cloud_preference", ""
            ),
            "expected_daily_traffic": blueprint.get(
                "expected_daily_traffic", ""
            ),
            "delivery_timeline_months": blueprint.get(
                "delivery_timeline_months", 6
            ),
            "data_hosting_country": blueprint.get(
                "data_hosting_country", ""
            ),
        },
        markdown_content=master_md,
        html_content=html_content,
        status="completed",
    )

    # ---------------------------------------------------------
    # 10. Update filesystem outputs
    # ---------------------------------------------------------
    save_output(
        f"{run_id}.html",
        html_content
    )

    save_output(
        f"{run_id}.md",
        master_md
    )

    save_output(
        "final_output.html",
        html_content
    )

    save_output(
        "final_output.md",
        master_md
    )

    # ---------------------------------------------------------
    # 11. Return complete cascade result
    # ---------------------------------------------------------
    return {
        "run_id": run_id,
        "status": "regenerated",
        "regenerated_agent": payload.agent,
        "cascade": agents_to_regenerate,
        "outputs": regenerated_outputs,
        "master_blueprint": master_md,
        "html": html_content,
    }

@router.delete("/{run_id}", status_code=200)
async def delete_blueprint(run_id: str):
    """Delete a specific blueprint from SQLite database and outputs."""
    if run_id == "final_output":
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the global fallback 'final_output'.",
        )

    db_deleted = delete_blueprint_by_run_id(run_id)

    outputs_dir = Path("outputs")
    file_deleted = False
    for ext in [".html", ".md"]:
        fpath = outputs_dir / f"{run_id}{ext}"
        if fpath.exists():
            fpath.unlink()
            file_deleted = True

    if not db_deleted and not file_deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Blueprint output for run_id '{run_id}' not found.",
        )

    return {
        "run_id": run_id,
        "status": "deleted",
        "message": f"Successfully deleted blueprint {run_id} from SQLite and storage."
    }