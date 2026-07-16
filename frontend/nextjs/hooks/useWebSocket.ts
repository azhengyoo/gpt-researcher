import { useState, useEffect, useCallback } from 'react';
import { Data, ChatBoxSettings } from '../types/data';
import { getHost } from '../helpers/getHost';

export const useWebSocket = (
  setOrderedData: React.Dispatch<React.SetStateAction<Data[]>>,
  setAnswer: React.Dispatch<React.SetStateAction<string>>, 
  setLoading: React.Dispatch<React.SetStateAction<boolean>>,
  setShowHumanFeedback: React.Dispatch<React.SetStateAction<boolean>>,
  setQuestionForHuman: React.Dispatch<React.SetStateAction<boolean | true>>
) => {
  const [socket, setSocket] = useState<WebSocket | null>(null);

  // Cleanup socket on unmount
  useEffect(() => {
    return () => {
      // Close socket on unmount if it exists and is open
      if (socket && socket.readyState === WebSocket.OPEN) {
        console.log('Closing WebSocket due to component unmount');
        socket.close(1000, "Component unmounted");
      }
    };
  }, [socket]);

  const initializeWebSocket = useCallback((
    promptValue: string, 
    chatBoxSettings: ChatBoxSettings
  ) => {
    // Close existing socket if any
    if (socket && socket.readyState === WebSocket.OPEN) {
      console.log('Closing existing WebSocket connection');
      socket.close(1000, "New connection requested");
    }

    const storedConfig = localStorage.getItem('apiVariables');
    const apiVariables = storedConfig ? JSON.parse(storedConfig) : {};

    if (typeof window !== 'undefined') {
      
      let fullHost = getHost()
      const protocol = fullHost.includes('https') ? 'wss:' : 'ws:'
      const cleanHost = fullHost.replace('http://', '').replace('https://', '')
      const ws_uri = `${protocol}//${cleanHost}/ws`

      console.log(`Creating new WebSocket connection to ${ws_uri}`);
      const newSocket = new WebSocket(ws_uri);
      setSocket(newSocket);

      // WebSocket connection opened handler
      newSocket.onopen = () => {
        console.log('WebSocket connection opened');
        
        const domainFilters = JSON.parse(localStorage.getItem('domainFilters') || '[]');
        const domains = domainFilters ? domainFilters.map((domain: any) => domain.value) : [];
        const { report_type, report_source, tone, mcp_enabled, mcp_configs, mcp_strategy, document_urls } = chatBoxSettings;
        
        // Start a new research
        try {
          console.log(`Starting new research for: ${promptValue}`);
          const dataToSend = { 
            task: promptValue,
            report_type, 
            report_source, 
            tone,
            query_domains: domains,
            document_urls: document_urls || [],
            mcp_enabled: mcp_enabled || false,
            mcp_strategy: mcp_strategy || "fast",
            mcp_configs: mcp_configs || []
          };
          
          // Make sure we have a properly formatted command with a space after start
          const message = `start ${JSON.stringify(dataToSend)}`;
          console.log(`Sending start message, length: ${message.length}`);
          newSocket.send(message);
        } catch (error) {
          console.error("Error preparing start message:", error);
        }
      };

      newSocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          
          if (data.type === 'error') {
            console.error(`Server error: ${data.output}`);
          } else if (data.type === 'human_feedback' && data.content === 'request') {
            setQuestionForHuman(data.output);
            setShowHumanFeedback(true);
          } else {
            const contentAndType = `${data.content}-${data.type}`;
            setOrderedData((prevOrder) => [...prevOrder, { ...data, contentAndType }]);

            if (data.type === 'report') {
              setAnswer((prev: string) => prev + data.output);
            } else if (data.type === 'report_complete') {
              // Replace entire report with the complete version (includes images)
              // Only update if output is provided; otherwise keep the streamed content
              if (data.output) {
                console.log('Received complete report with images');
                setAnswer(data.output);
              }
            } else if (data.type === 'path') {
              setLoading(false);
            }
          }
        } catch (error) {
          console.error('Error parsing WebSocket message:', error, event.data);
        }
      };

      newSocket.onclose = (event) => {
        console.log(`WebSocket connection closed: code=${event.code}, reason=${event.reason}`);
        setSocket(null);
      };

      newSocket.onerror = (error) => {
        console.error('WebSocket error:', error);
      };
    }
  }, [socket, setOrderedData, setAnswer, setLoading, setShowHumanFeedback, setQuestionForHuman]);

  return { socket, setSocket, initializeWebSocket };
};