// LogMessage.tsx
import Accordion from '../../Task/Accordion';
import { useEffect, useState, useRef } from 'react';
import { markdownToHtml } from '../../../helpers/markdownHelper';
import ImagesAlbum from '../../Images/ImagesAlbum';
import Image from "next/image";

type ProcessedData = {
  field: string;
  htmlContent: string;
  isMarkdown: boolean;
};

type Log = {
  header: string;
  text: string;
  processedData?: ProcessedData[];
  metadata?: any;
};

interface LogMessageProps {
  logs: Log[];
}

const LogMessage: React.FC<LogMessageProps> = ({ logs }) => {
  const [processedLogs, setProcessedLogs] = useState<Log[]>([]);
  const prevLogCountRef = useRef(0);

  useEffect(() => {
    const processLogs = async () => {
      if (!logs || logs.length === 0) {
        setProcessedLogs([]);
        prevLogCountRef.current = 0;
        return;
      }

      // Only process new logs since last update (incremental processing)
      const newLogs = logs.slice(prevLogCountRef.current);
      if (newLogs.length === 0) return;

      const processedNewLogs = await Promise.all(
        newLogs.map(async (log) => {
          try {
            if (log.header === 'differences' && log.text) {
              const data = JSON.parse(log.text).data;
              const processedData = await Promise.all(
                Object.keys(data).map(async (field) => {
                  const fieldValue = data[field].after || data[field].before;
                  if (!plainTextFields.includes(field)) {
                    const htmlContent = await markdownToHtml(fieldValue);
                    return { field, htmlContent, isMarkdown: true };
                  }
                  return { field, htmlContent: fieldValue, isMarkdown: false };
                })
              );
              return { ...log, processedData };
            }
            return log;
          } catch (error) {
            console.error('Error processing log:', error);
            return log;
          }
        })
      );

      setProcessedLogs(prev => [...prev, ...processedNewLogs]);
      prevLogCountRef.current = logs.length;
    };

    processLogs();
  }, [logs]);

  return (
    <>
      {processedLogs.map((log, index) => {
        if (log.header === 'subquery_context_window' || log.header === 'differences') {
          return <Accordion key={index} logs={[log]} />;
        } else if (log.header !== 'selected_images' && log.header !== 'scraping_images') {
          return (
            <div
              key={index}
              className="w-full max-w-4xl mx-auto rounded-lg pt-2 mt-3 pb-2 px-4 bg-gray-900 shadow-md"
            >
              <p className="py-3 text-base leading-relaxed text-white dark:text-white">
                {log.text}
              </p>
            </div>
          );
        }
        return null;
      })}
    </>
  );
};

const plainTextFields = ['task', 'sections', 'headers', 'sources', 'research_data'];

export default LogMessage;