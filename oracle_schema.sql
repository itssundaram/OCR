-- ==========================================
-- DOCINT ORACLE SCHEMA CLEANUP & CREATION
-- ==========================================

-- 1. DROP EXISTING OBJECTS (To ensure a clean slate)
-- Ignore errors if objects do not exist
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE PROCESSING_RESULTS CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE PROCESSING_JOBS CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE DOCUMENTS CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE TEMPLATES CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE DEPARTMENTS CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
-- Drop deprecated tables if they exist
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE DOCUMENT_TEMPLATES CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE DOCUMENT_TYPES CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/

-- Drop Sequences
BEGIN
   EXECUTE IMMEDIATE 'DROP SEQUENCE departments_id_seq';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP SEQUENCE doc_templates_id_seq';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
BEGIN
   EXECUTE IMMEDIATE 'DROP SEQUENCE proc_results_id_seq';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/


-- ==========================================
-- 2. CREATE SEQUENCES
-- ==========================================

CREATE SEQUENCE departments_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE doc_templates_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE proc_results_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE field_ext_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE table_ext_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE proc_event_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE dept_urls_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE tmpl_fields_id_seq START WITH 1 INCREMENT BY 1 NOCACHE;


-- ==========================================
-- 3. CREATE TABLES
-- ==========================================

-- DEPARTMENTS
CREATE TABLE DEPARTMENTS (
    id NUMBER NOT NULL,
    slug VARCHAR2(50) NOT NULL,
    name VARCHAR2(200) NOT NULL,
    description VARCHAR2(1000),
    color VARCHAR2(20) DEFAULT '#6366f1' NOT NULL,
    icon VARCHAR2(50) DEFAULT 'folder' NOT NULL,
    is_active NUMBER DEFAULT 1 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_departments PRIMARY KEY (id),
    CONSTRAINT uq_dept_slug UNIQUE (slug),
    CONSTRAINT ck_dept_active CHECK (is_active IN (0, 1))
);

CREATE INDEX idx_dept_slug ON DEPARTMENTS(slug);


-- TEMPLATES
CREATE TABLE TEMPLATES (
    id NUMBER NOT NULL,
    department_id NUMBER NOT NULL,
    code VARCHAR2(100) NOT NULL,
    version NUMBER NOT NULL,
    template_json CLOB NOT NULL,
    extraction_instructions CLOB,
    is_active NUMBER DEFAULT 0 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_templates PRIMARY KEY (id),
    CONSTRAINT fk_tmpl_dept FOREIGN KEY (department_id) REFERENCES DEPARTMENTS(id) ON DELETE CASCADE,
    CONSTRAINT uq_tmpl_dept_code_ver UNIQUE (department_id, code, version),
    CONSTRAINT ck_tmpl_active CHECK (is_active IN (0, 1)),
    CONSTRAINT ck_tmpl_json CHECK (template_json IS JSON)
);

CREATE INDEX idx_tmpl_dept_code ON TEMPLATES(department_id, code);
CREATE INDEX idx_tmpl_active_lookup ON TEMPLATES(department_id, code, is_active);
CREATE UNIQUE INDEX uq_one_active_tmpl_per_code ON TEMPLATES (
    CASE WHEN is_active = 1 THEN department_id ELSE NULL END, 
    CASE WHEN is_active = 1 THEN code ELSE NULL END
);


-- DOCUMENTS
CREATE TABLE DOCUMENTS (
    id VARCHAR2(36) NOT NULL,
    original_filename VARCHAR2(500) NOT NULL,
    stored_filename VARCHAR2(500) NOT NULL,
    file_path VARCHAR2(2000) NOT NULL,
    file_type VARCHAR2(50) NOT NULL,
    file_size_bytes NUMBER NOT NULL,
    page_count NUMBER,
    template_id NUMBER NOT NULL,
    status VARCHAR2(50) DEFAULT 'PENDING' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_documents PRIMARY KEY (id),
    CONSTRAINT fk_doc_template FOREIGN KEY (template_id) REFERENCES TEMPLATES(id),
    CONSTRAINT ck_doc_status CHECK (status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'WARNING', 'FAILED'))
);

CREATE INDEX idx_doc_template ON DOCUMENTS(template_id);
CREATE INDEX idx_doc_status ON DOCUMENTS(status);
CREATE INDEX idx_doc_created_at ON DOCUMENTS(created_at);


-- PROCESSING_JOBS
CREATE TABLE PROCESSING_JOBS (
    id VARCHAR2(36) NOT NULL,
    document_id VARCHAR2(36) NOT NULL,
    template_id NUMBER NOT NULL,
    template_version NUMBER NOT NULL,
    pipeline_mode VARCHAR2(50),
    page_count NUMBER,
    status VARCHAR2(50) DEFAULT 'QUEUED' NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_ms NUMBER,
    ocr_engine VARCHAR2(100),
    ocr_version VARCHAR2(100),
    ai_engine VARCHAR2(100),
    ai_model VARCHAR2(200),
    overall_confidence NUMBER,
    error_code VARCHAR2(100),
    error_message CLOB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_processing_jobs PRIMARY KEY (id),
    CONSTRAINT fk_pj_document FOREIGN KEY (document_id) REFERENCES DOCUMENTS(id) ON DELETE CASCADE,
    CONSTRAINT fk_pj_template FOREIGN KEY (template_id) REFERENCES TEMPLATES(id),
    CONSTRAINT ck_pj_status CHECK (status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'WARNING', 'FAILED'))
);

CREATE INDEX idx_pj_document ON PROCESSING_JOBS(document_id);
CREATE INDEX idx_pj_template ON PROCESSING_JOBS(template_id);
CREATE INDEX idx_pj_status ON PROCESSING_JOBS(status);
CREATE INDEX idx_pj_created_at ON PROCESSING_JOBS(created_at);


-- PROCESSING_RESULTS
CREATE TABLE PROCESSING_RESULTS (
    id NUMBER NOT NULL,
    job_id VARCHAR2(36) NOT NULL,
    extracted_json CLOB NOT NULL,
    confidence_json CLOB NOT NULL,
    ocr_metadata_json CLOB,
    validation_warnings CLOB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_processing_results PRIMARY KEY (id),
    CONSTRAINT fk_pr_job FOREIGN KEY (job_id) REFERENCES PROCESSING_JOBS(id) ON DELETE CASCADE,
    CONSTRAINT uq_pr_job UNIQUE (job_id),
    CONSTRAINT ck_pr_ext_json CHECK (extracted_json IS JSON),
    CONSTRAINT ck_pr_conf_json CHECK (confidence_json IS JSON),
    CONSTRAINT ck_pr_ocr_json CHECK (ocr_metadata_json IS JSON)
);

-- DOCUMENT_PAGES
CREATE TABLE DOCUMENT_PAGES (
    id VARCHAR2(36) NOT NULL,
    document_id VARCHAR2(36) NOT NULL,
    page_number NUMBER NOT NULL,
    width NUMBER,
    height NUMBER,
    image_asset_id VARCHAR2(36),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_document_pages PRIMARY KEY (id),
    CONSTRAINT fk_dp_document FOREIGN KEY (document_id) REFERENCES DOCUMENTS(id) ON DELETE CASCADE
);

-- ASSETS
CREATE TABLE ASSETS (
    id VARCHAR2(36) NOT NULL,
    asset_type VARCHAR2(50) NOT NULL,
    file_path VARCHAR2(2000) NOT NULL,
    mime_type VARCHAR2(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_assets PRIMARY KEY (id)
);

-- PIPELINE_RUNS
CREATE TABLE PIPELINE_RUNS (
    id VARCHAR2(36) NOT NULL,
    job_id VARCHAR2(36) NOT NULL,
    pipeline_name VARCHAR2(100) NOT NULL,
    status VARCHAR2(50) DEFAULT 'QUEUED',
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    error_message CLOB,
    overall_confidence NUMBER,
    metadata_json CLOB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_pipeline_runs PRIMARY KEY (id),
    CONSTRAINT fk_pr_job2 FOREIGN KEY (job_id) REFERENCES PROCESSING_JOBS(id) ON DELETE CASCADE,
    CONSTRAINT ck_pr_metadata_json CHECK (metadata_json IS JSON)
);

-- FIELD_EXTRACTIONS
CREATE TABLE FIELD_EXTRACTIONS (
    id NUMBER NOT NULL,
    job_id VARCHAR2(36) NOT NULL,
    pipeline_run_id VARCHAR2(36),
    page_id VARCHAR2(36),
    field_name VARCHAR2(500) NOT NULL,
    field_value CLOB,
    normalized_value CLOB,
    confidence NUMBER,
    extraction_method VARCHAR2(100),
    fallback_method VARCHAR2(100),
    bbox_json CLOB,
    crop_asset_id VARCHAR2(36),
    is_final NUMBER DEFAULT 0 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_field_extractions PRIMARY KEY (id),
    CONSTRAINT fk_fe_job FOREIGN KEY (job_id) REFERENCES PROCESSING_JOBS(id) ON DELETE CASCADE,
    CONSTRAINT fk_fe_pr FOREIGN KEY (pipeline_run_id) REFERENCES PIPELINE_RUNS(id) ON DELETE SET NULL,
    CONSTRAINT fk_fe_page FOREIGN KEY (page_id) REFERENCES DOCUMENT_PAGES(id) ON DELETE SET NULL,
    CONSTRAINT fk_fe_asset FOREIGN KEY (crop_asset_id) REFERENCES ASSETS(id) ON DELETE SET NULL,
    CONSTRAINT ck_fe_bbox_json CHECK (bbox_json IS JSON)
);

-- TABLE_EXTRACTIONS
CREATE TABLE TABLE_EXTRACTIONS (
    id NUMBER NOT NULL,
    job_id VARCHAR2(36) NOT NULL,
    pipeline_run_id VARCHAR2(36),
    page_id VARCHAR2(36),
    table_index NUMBER DEFAULT 0,
    extraction_method VARCHAR2(100),
    confidence NUMBER,
    bbox_json CLOB,
    crop_asset_id VARCHAR2(36),
    headers_json CLOB,
    rows_json CLOB,
    markdown CLOB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_table_extractions PRIMARY KEY (id),
    CONSTRAINT fk_te_job FOREIGN KEY (job_id) REFERENCES PROCESSING_JOBS(id) ON DELETE CASCADE,
    CONSTRAINT fk_te_pr FOREIGN KEY (pipeline_run_id) REFERENCES PIPELINE_RUNS(id) ON DELETE SET NULL,
    CONSTRAINT fk_te_page FOREIGN KEY (page_id) REFERENCES DOCUMENT_PAGES(id) ON DELETE SET NULL,
    CONSTRAINT fk_te_asset FOREIGN KEY (crop_asset_id) REFERENCES ASSETS(id) ON DELETE SET NULL,
    CONSTRAINT ck_te_bbox_json CHECK (bbox_json IS JSON),
    CONSTRAINT ck_te_headers_json CHECK (headers_json IS JSON),
    CONSTRAINT ck_te_rows_json CHECK (rows_json IS JSON)
);

-- PROCESSING_EVENTS
CREATE TABLE PROCESSING_EVENTS (
    id NUMBER NOT NULL,
    job_id VARCHAR2(36) NOT NULL,
    pipeline_run_id VARCHAR2(36),
    page_id VARCHAR2(36),
    event_type VARCHAR2(100) NOT NULL,
    status VARCHAR2(50),
    message VARCHAR2(2000),
    metadata_json CLOB,
    duration_ms NUMBER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_processing_events PRIMARY KEY (id),
    CONSTRAINT fk_pe_job FOREIGN KEY (job_id) REFERENCES PROCESSING_JOBS(id) ON DELETE CASCADE,
    CONSTRAINT ck_pe_metadata_json CHECK (metadata_json IS JSON)
);
CREATE INDEX idx_pe_job ON PROCESSING_EVENTS(job_id);
CREATE INDEX idx_pe_created ON PROCESSING_EVENTS(created_at);

-- DEPARTMENT_URLS
CREATE TABLE DEPARTMENT_URLS (
    id NUMBER NOT NULL,
    department_id NUMBER NOT NULL,
    url VARCHAR2(2000) NOT NULL,
    label VARCHAR2(500),
    template_id NUMBER,
    is_active NUMBER DEFAULT 1,
    last_checked_at TIMESTAMP WITH TIME ZONE,
    last_status VARCHAR2(50),
    last_status_code NUMBER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_department_urls PRIMARY KEY (id),
    CONSTRAINT fk_du_dept FOREIGN KEY (department_id) REFERENCES DEPARTMENTS(id) ON DELETE CASCADE,
    CONSTRAINT fk_du_tmpl FOREIGN KEY (template_id) REFERENCES TEMPLATES(id) ON DELETE SET NULL
);

-- TEMPLATE_FIELDS
CREATE TABLE TEMPLATE_FIELDS (
    id NUMBER NOT NULL,
    template_id NUMBER NOT NULL,
    field_name VARCHAR2(500) NOT NULL,
    field_type VARCHAR2(100),
    is_required NUMBER DEFAULT 0,
    confidence_threshold NUMBER DEFAULT 0.7,
    validation_regex VARCHAR2(2000),
    description CLOB,
    extraction_hint CLOB,
    field_order NUMBER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_template_fields PRIMARY KEY (id),
    CONSTRAINT fk_tf_tmpl FOREIGN KEY (template_id) REFERENCES TEMPLATES(id) ON DELETE CASCADE
);

-- ==========================================
-- 4. INSERT DEFAULT SEED DATA
-- ==========================================

INSERT INTO DEPARTMENTS (id, slug, name, description, color, icon) VALUES (departments_id_seq.NEXTVAL, 'MMS', 'MMS', 'MMS Documents', '#6366f1', 'folder');
INSERT INTO DEPARTMENTS (id, slug, name, description, color, icon) VALUES (departments_id_seq.NEXTVAL, 'PSG', 'PSG', 'PSG Documents', '#10b981', 'folder');
INSERT INTO DEPARTMENTS (id, slug, name, description, color, icon) VALUES (departments_id_seq.NEXTVAL, 'TMS', 'TMS', 'TMS Documents', '#f59e0b', 'folder');
INSERT INTO DEPARTMENTS (id, slug, name, description, color, icon) VALUES (departments_id_seq.NEXTVAL, 'CUE_MMS', 'CUE_MMS', 'CUE MMS Documents', '#6366f1', 'folder');
INSERT INTO DEPARTMENTS (id, slug, name, description, color, icon) VALUES (departments_id_seq.NEXTVAL, 'CUE_PSG', 'CUE_PSG', 'CUE PSG Documents', '#ef4444', 'folder');
INSERT INTO DEPARTMENTS (id, slug, name, description, color, icon) VALUES (departments_id_seq.NEXTVAL, 'CUE_TMS', 'CUE_TMS', 'CUE TMS Documents', '#f59e0b', 'folder');

COMMIT;
