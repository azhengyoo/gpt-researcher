import React, { useState, useEffect } from "react";
import FileUpload from "../Settings/FileUpload";
import ToneSelector from "../Settings/ToneSelector";
import MCPSelector from "../Settings/MCPSelector";
import LayoutSelector from "../Settings/LayoutSelector";
import DomainFilter from "./DomainFilter";
import { useAnalytics } from "../../hooks/useAnalytics";
import { ChatBoxSettings, Domain, MCPConfig } from '@/types/data';

interface ResearchFormProps {
  chatBoxSettings: ChatBoxSettings;
  setChatBoxSettings: React.Dispatch<React.SetStateAction<ChatBoxSettings>>;
  onFormSubmit?: (
    task: string,
    reportType: string,
    reportSource: string,
    domains: Domain[],
    documentUrls: string[]
  ) => void;
}

export default function ResearchForm({
  chatBoxSettings,
  setChatBoxSettings,
  onFormSubmit,
}: ResearchFormProps) {
  const { trackResearchQuery } = useAnalytics();
  const [task, setTask] = useState("");
  const [newDomain, setNewDomain] = useState('');

  // Destructure necessary fields from chatBoxSettings
  let { report_type, report_source, tone, layoutType } = chatBoxSettings;

  const [domains, setDomains] = useState<Domain[]>([]);
  const [documentUrls, setDocumentUrls] = useState(
    (chatBoxSettings.document_urls || []).join('\n')
  );

  // Load domains from localStorage after hydration
  useEffect(() => {
    try {
      const saved = localStorage.getItem('domainFilters');
      if (saved) {
        setDomains(JSON.parse(saved));
      }
    } catch (e) {
      console.error('Error loading domain filters:', e);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem('domainFilters', JSON.stringify(domains));
    setChatBoxSettings(prev => ({
      ...prev,
      domains: domains.map(domain => domain.value)
    }));
  }, [domains, setChatBoxSettings]);

  const handleAddDomain = (e: React.FormEvent) => {
    e.preventDefault();
    if (newDomain.trim()) {
      setDomains([...domains, { value: newDomain.trim() }]);
      setNewDomain('');
    }
  };

  const handleRemoveDomain = (domainToRemove: string) => {
    setDomains(domains.filter(domain => domain.value !== domainToRemove));
  };

  const onFormChange = (e: { target: { name: any; value: any } }) => {
    const { name, value } = e.target;
    setChatBoxSettings((prevSettings: any) => ({
      ...prevSettings,
      [name]: value,
    }));
  };

  const onToneChange = (e: { target: { value: any } }) => {
    const { value } = e.target;
    setChatBoxSettings((prevSettings: any) => ({
      ...prevSettings,
      tone: value,
    }));
  };

  const onLayoutChange = (e: { target: { value: any } }) => {
    const { value } = e.target;
    setChatBoxSettings((prevSettings: any) => ({
      ...prevSettings,
      layoutType: value,
    }));
  };

  const onMCPChange = (enabled: boolean, configs: MCPConfig[]) => {
    setChatBoxSettings((prevSettings: any) => ({
      ...prevSettings,
      mcp_enabled: enabled,
      mcp_configs: configs,
    }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (onFormSubmit) {
      const updatedSettings = {
        ...chatBoxSettings,
        domains: domains.map(domain => domain.value)
      };
      setChatBoxSettings(updatedSettings);
      const docUrls = documentUrls
        .split('\n')
        .map(url => url.trim())
        .filter(url => url.length > 0);
      onFormSubmit(task, report_type, report_source, domains, docUrls);
    }
  };

  return (
    <form
      method="POST"
      className="report_settings_static mt-3"
      onSubmit={handleSubmit}
    >
      <div className="form-group">
        <label htmlFor="report_type" className="agent_question">
          报告类型{" "}
        </label>
        <select
          name="report_type"
          value={report_type}
          onChange={onFormChange}
          className="form-control-static"
          required
        >
          <option value="research_report">
            摘要 - 快速简洁（约2分钟）
          </option>
          <option value="deep">深度研究报告</option>
          <option value="multi_agents">多智能体报告</option>
          <option value="detailed_report">
            详细 - 深入全面（约5分钟）
          </option>
        </select>
      </div>

      <div className="form-group">
        <label htmlFor="report_source" className="agent_question">
          报告来源{" "}
        </label>
        <select
          name="report_source"
          value={report_source}
          onChange={onFormChange}
          className="form-control-static"
          required
        >
          <option value="web">互联网</option>
          <option value="online_docs">在线文档</option>
          <option value="local">我的文档</option>
          <option value="hybrid">混合来源</option>
        </select>
      </div>

      

      {report_source === "online_docs" || report_source === "hybrid" ? (
        <div className="form-group">
          <label htmlFor="document_urls" className="agent_question">
            在线文档地址（每行一个URL）{" "}
          </label>
          <textarea
            id="document_urls"
            name="document_urls"
            value={documentUrls}
            onChange={(e) => {
              setDocumentUrls(e.target.value);
              const urls = e.target.value
                .split('\n')
                .map(url => url.trim())
                .filter(url => url.length > 0);
              setChatBoxSettings((prev: any) => ({
                ...prev,
                document_urls: urls,
              }));
            }}
            className="form-control-static w-full min-h-[80px]"
            placeholder="https://docs.google.com/document/d/...
https://example.com/report.pdf"
            rows={4}
          />
        </div>
      ) : null}

      {report_source === "local" || report_source === "hybrid" ? (
        <FileUpload />
      ) : null}
      
      <ToneSelector tone={tone} onToneChange={onToneChange} />

      <MCPSelector 
        mcpEnabled={chatBoxSettings.mcp_enabled || false}
        mcpConfigs={chatBoxSettings.mcp_configs || []}
        onMCPChange={onMCPChange}
      />
      
      <LayoutSelector layoutType={layoutType || 'copilot'} onLayoutChange={onLayoutChange} />

      {/** TODO: move the below to its own component */}
      {(chatBoxSettings.report_source === "web" || chatBoxSettings.report_source === "hybrid") && (
        <DomainFilter
          domains={domains}
          newDomain={newDomain}
          setNewDomain={setNewDomain}
          onAddDomain={handleAddDomain}
          onRemoveDomain={handleRemoveDomain}
        />
      )}
    </form>
  );
}
