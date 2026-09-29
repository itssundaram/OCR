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
    template_id NUMBER NOT NULL,
    status VARCHAR2(50) DEFAULT 'PENDING' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_documents PRIMARY KEY (id),
    CONSTRAINT fk_doc_template FOREIGN KEY (template_id) REFERENCES TEMPLATES(id),
    CONSTRAINT ck_doc_status CHECK (status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED'))
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
    CONSTRAINT ck_pj_status CHECK (status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'FAILED'))
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
