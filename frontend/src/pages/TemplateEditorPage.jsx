import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Save, Plus, Trash2, GripVertical } from 'lucide-react';
import { templatesService } from '../services/templates';
import { departmentsService } from '../services/departments';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Loader } from '../components/ui/Loader';

const TemplateEditorPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const isNew = id === 'new';

  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  
  const [departments, setDepartments] = useState([]);
  
  const [formData, setFormData] = useState({
    department_slug: '',
    code: '',
    extraction_instructions: '',
    fields: []
  });

  useEffect(() => {
    const init = async () => {
      try {
        const deptsRes = await departmentsService.listAll();
        setDepartments(deptsRes.data || []);

        if (!isNew) {
          const tRes = await templatesService.getTemplate(id);
          const t = tRes.data;
          setFormData({
            department_slug: deptsRes.data.find(d => d.id === t.department_id)?.slug || '',
            code: t.code,
            extraction_instructions: t.extraction_instructions || '',
            fields: t.template_json?.fields || []
          });
        }
      } catch (err) {
        setError(err.message || 'Failed to load data');
      } finally {
        setLoading(false);
      }
    };
    init();
  }, [id, isNew]);

  const handleFieldChange = (index, key, value) => {
    const newFields = [...formData.fields];
    newFields[index][key] = value;
    setFormData({ ...formData, fields: newFields });
  };

  const addField = () => {
    setFormData({
      ...formData,
      fields: [
        ...formData.fields,
        { name: '', type: 'text', description: '', required: false, example_value: '' }
      ]
    });
  };

  const removeField = (index) => {
    const newFields = [...formData.fields];
    newFields.splice(index, 1);
    setFormData({ ...formData, fields: newFields });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        code: formData.code,
        extraction_instructions: formData.extraction_instructions || null,
        template_json: { fields: formData.fields }
      };

      if (isNew) {
        if (!formData.department_slug) throw new Error("Please select a department");
        await templatesService.createTemplate(formData.department_slug, payload);
      } else {
        await templatesService.updateTemplate(id, payload);
      }
      navigate('/manage');
    } catch (err) {
      setError(err.message || 'Failed to save template');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="p-12 flex justify-center"><Loader text="Loading..." /></div>;

  return (
    <div className="flex flex-col gap-6 max-w-5xl mx-auto p-4 w-full h-full animate-fade-in-up">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-4">
          <button className="icon-btn" onClick={() => navigate('/manage')}>
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-2xl font-bold text-primary">
            {isNew ? 'Create New Template' : `Edit Template: ${formData.code}`}
          </h1>
        </div>
        <button 
          className="btn btn-primary flex items-center gap-2" 
          onClick={handleSave} 
          disabled={saving}
        >
          {saving ? <Loader size={16} /> : <Save size={16} />}
          {isNew ? 'Create Template' : 'Save Changes'}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger text-sm">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader><CardTitle>Template Details</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-4">
            {isNew && (
              <div>
                <label className="block text-sm font-medium text-secondary mb-1">Department</label>
                <select 
                  className="input-field w-full"
                  value={formData.department_slug}
                  onChange={(e) => setFormData({ ...formData, department_slug: e.target.value })}
                >
                  <option value="">Select a department...</option>
                  {departments.map(d => (
                    <option key={d.id} value={d.slug}>{d.name}</option>
                  ))}
                </select>
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-secondary mb-1">Template Code</label>
              <input 
                type="text" 
                className="input-field w-full" 
                placeholder="e.g. INVOICE_V1"
                value={formData.code}
                onChange={(e) => setFormData({ ...formData, code: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-secondary mb-1">Extraction Instructions (Optional)</label>
              <textarea 
                className="input-field w-full h-24"
                placeholder="Specific instructions for the extraction model..."
                value={formData.extraction_instructions}
                onChange={(e) => setFormData({ ...formData, extraction_instructions: e.target.value })}
              />
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between border-b border-border-color pb-4">
          <CardTitle>Extraction Fields</CardTitle>
          <button className="btn btn-secondary flex items-center gap-2 text-sm" onClick={addField}>
            <Plus size={16} /> Add Field
          </button>
        </CardHeader>
        <CardContent className="pt-6">
          {formData.fields.length === 0 ? (
            <div className="text-center py-8 text-secondary italic border-2 border-dashed border-border-color rounded-lg">
              No fields defined. Add a field to start building the extraction schema.
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {formData.fields.map((field, index) => (
                <div key={index} className="flex gap-4 items-start p-4 bg-bg-secondary rounded-lg border border-border-color group">
                  <div className="pt-2 text-secondary cursor-grab active:cursor-grabbing opacity-50 group-hover:opacity-100 transition-opacity">
                    <GripVertical size={20} />
                  </div>
                  
                  <div className="flex-1 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-secondary mb-1">Field Name (Key)</label>
                      <input 
                        type="text" 
                        className="input-field w-full text-sm" 
                        placeholder="e.g. invoice_number"
                        value={field.name}
                        onChange={(e) => handleFieldChange(index, 'name', e.target.value)}
                      />
                    </div>
                    
                    <div>
                      <label className="block text-xs font-medium text-secondary mb-1">Data Type</label>
                      <select 
                        className="input-field w-full text-sm"
                        value={field.type}
                        onChange={(e) => handleFieldChange(index, 'type', e.target.value)}
                      >
                        <option value="text">Text (String)</option>
                        <option value="number">Number</option>
                        <option value="date">Date</option>
                        <option value="boolean">Boolean</option>
                        <option value="table">Table (Array of Objects)</option>
                        <option value="list">List (Array of Strings)</option>
                      </select>
                    </div>

                    <div className="lg:col-span-2">
                      <label className="block text-xs font-medium text-secondary mb-1">Description (for AI)</label>
                      <input 
                        type="text" 
                        className="input-field w-full text-sm" 
                        placeholder="Explain what this field is..."
                        value={field.description}
                        onChange={(e) => handleFieldChange(index, 'description', e.target.value)}
                      />
                    </div>

                    <div className="lg:col-span-2">
                      <label className="block text-xs font-medium text-secondary mb-1">Example Value</label>
                      <input 
                        type="text" 
                        className="input-field w-full text-sm font-mono" 
                        placeholder="e.g. INV-2023-001"
                        value={field.example_value || ''}
                        onChange={(e) => handleFieldChange(index, 'example_value', e.target.value)}
                      />
                    </div>
                    
                    <div className="flex items-end pb-2">
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input 
                          type="checkbox" 
                          className="rounded border-border-color text-primary-accent focus:ring-primary-accent"
                          checked={field.required || false}
                          onChange={(e) => handleFieldChange(index, 'required', e.target.checked)}
                        />
                        <span className="text-primary font-medium">Required Field</span>
                      </label>
                    </div>
                  </div>

                  <button 
                    className="p-2 text-secondary hover:text-danger hover:bg-danger hover:bg-opacity-10 rounded-md transition-colors mt-6"
                    onClick={() => removeField(index)}
                    title="Remove Field"
                  >
                    <Trash2 size={18} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default TemplateEditorPage;
