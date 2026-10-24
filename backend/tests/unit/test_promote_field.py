"""Found-on-document field -> template field: additive, logged, collision-safe, job result follows."""
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.core.exceptions import DocIntError
from app.db.database import Base
from app.db.models import Department, Document, FieldExtraction, ProcessingJob, ProcessingResult, Template
from app.services.export_json import build_export
from app.services.template_fields import add_field, infer_type, slug_name


def test_add_field_is_additive_logged_and_defined_by_its_label():
    tj = {"fields": [{"name": "iv_no", "type": "text", "description": "d"}], "learned_anchors": {"x": 1}}
    out, f = add_field(tj, name="Stn", label="Stn", value="c/o 56 APO", field_type=None, job_id="J")
    assert [x["name"] for x in out["fields"]] == ["iv_no", "stn"]
    assert out["learned_anchors"] == {"x": 1}                      # nothing else touched
    assert f["type"] == "text" and f["origin"]["job_id"] == "J"
    assert out["change_log"][-1]["action"] == "add_field" and out["change_log"][-1]["field"] == "stn"
    assert tj["fields"] == [{"name": "iv_no", "type": "text", "description": "d"}]   # the input was not mutated


def test_label_becomes_an_alias_when_the_name_differs():
    _, f = add_field({"fields": []}, name="station", label="Stn", value="x", field_type=None, job_id="J")
    assert f["aliases"] == ["Stn"]


def test_name_and_label_clashes_are_refused():
    tj = {"fields": [{"name": "iv_no", "aliases": ["IV No"]}]}
    with pytest.raises(DocIntError) as e:
        add_field(tj, name="iv_no", label="X", value="1", field_type=None, job_id="J")
    assert e.value.code == "FIELD_ALREADY_IN_TEMPLATE"
    with pytest.raises(DocIntError) as e:
        add_field(tj, name="voucher", label="IV No", value="1", field_type=None, job_id="J")
    assert e.value.code == "LABEL_ALREADY_IN_TEMPLATE"
    with pytest.raises(DocIntError) as e:
        add_field({"fields": []}, name="!!", label="X", value="1", field_type=None, job_id="J")
    assert e.value.code == "INVALID_FIELD_NAME"


def test_type_guess_and_slug():
    assert infer_type("12 Jun 2025") == "date" and infer_type("31421") == "number" and infer_type("c/o 99 APO") == "text"
    assert slug_name("Veh BA No.") == "veh_ba_no" and slug_name("2nd Unit") == "f_2nd_unit"


@pytest.fixture()
def env():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    dept = Department(slug="TEST", name="T"); db.add(dept); db.flush()
    tmpl = Template(department_id=dept.id, code="t", version=1, template_json={"fields": [{"name": "iv_no", "type": "text", "description": "d"}]}, is_active=1)
    db.add(tmpl); db.flush()
    doc = Document(id=str(uuid.uuid4()), original_filename="a.pdf", stored_filename="a", file_path="/x", file_type="application/pdf",
                   file_size_bytes=1, template_id=tmpl.id, status="COMPLETED")
    db.add(doc); db.flush()
    job = ProcessingJob(id=str(uuid.uuid4()), document_id=doc.id, template_id=tmpl.id, template_version=1, status="COMPLETED", pipeline_mode="fast_ocr")
    db.add(job)
    db.add(ProcessingResult(job_id=job.id, extracted_json={"fields": {
        "iv_no": {"value": "20/4", "confidence": 0.9},
        "stn": {"value": "c/o 56 APO", "label": "Stn", "source": "discovered", "confidence": 0.8}}, "tables": []},
        confidence_json=[], ocr_metadata_json={}))
    db.add(FieldExtraction(job_id=job.id, field_name="stn", field_value="c/o 56 APO", confidence=0.8, extraction_method="kv_discovery", is_final=1))
    db.commit()
    yield db, tmpl, job
    db.close()


def test_endpoint_adds_the_field_and_moves_it_in_the_result(env):
    from app.api.routes.extract import PromoteFieldRequest, promote_found_field
    db, tmpl, job = env
    r = promote_found_field(job.id, PromoteFieldRequest(field_key="stn", name="station"), db)
    assert r.data["field_name"] == "station"
    assert [f["name"] for f in db.get(Template, tmpl.id).template_json["fields"]] == ["iv_no", "station"]
    res = db.execute(select(ProcessingResult).where(ProcessingResult.job_id == job.id)).scalars().first()
    assert "stn" not in res.extracted_json["fields"] and res.extracted_json["fields"]["station"]["value"] == "c/o 56 APO"
    out = build_export(res.extracted_json)
    assert out["fields"]["station"] == "c/o 56 APO" and "station" not in out["additional_fields"]
    assert db.execute(select(FieldExtraction.field_name).where(FieldExtraction.job_id == job.id)).scalars().all() == ["station"]


def test_endpoint_refuses_a_template_field_and_a_clash(env):
    from app.api.routes.extract import PromoteFieldRequest, promote_found_field
    db, tmpl, job = env
    with pytest.raises(DocIntError) as e:
        promote_found_field(job.id, PromoteFieldRequest(field_key="iv_no"), db)
    assert e.value.code == "NOT_A_FOUND_FIELD"
    with pytest.raises(DocIntError) as e:
        promote_found_field(job.id, PromoteFieldRequest(field_key="stn", name="iv_no"), db)
    assert e.value.code == "FIELD_ALREADY_IN_TEMPLATE"
    assert [f["name"] for f in db.get(Template, tmpl.id).template_json["fields"]] == ["iv_no"]     # nothing was changed
