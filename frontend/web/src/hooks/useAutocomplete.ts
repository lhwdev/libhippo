import { useState, useEffect, useCallback, KeyboardEvent } from "react";
import { CommandItem, FileItem } from "../types/api";

export type AutocompleteTrigger = "command" | "file" | null;

export function useAutocomplete() {
  const [commands, setCommands] = useState<CommandItem[]>([]);
  const [activeTrigger, setActiveTrigger] = useState<AutocompleteTrigger>(null);
  const [query, setQuery] = useState("");
  const [fileSuggestions, setFileSuggestions] = useState<FileItem[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [isOpen, setIsOpen] = useState(false);

  // Load available commands on mount
  useEffect(() => {
    fetch("/api/autocomplete/commands")
      .then((res) => res.json())
      .then((data) => {
        if (data.commands) {
          setCommands(data.commands);
        }
      })
      .catch((err) => console.error("Failed to load autocomplete commands:", err));
  }, []);

  // Search files when query changes under file trigger
  useEffect(() => {
    if (activeTrigger === "file") {
      fetch(`/api/autocomplete/files?q=${encodeURIComponent(query)}`)
        .then((res) => res.json())
        .then((data) => {
          if (data.files) {
            setFileSuggestions(data.files);
            setSelectedIndex(0);
          }
        })
        .catch((err) => console.error("Failed to load files:", err));
    }
  }, [activeTrigger, query]);

  // Compute filtered suggestions
  const filteredCommands = commands.filter(
    (c) =>
      c.command.toLowerCase().includes(query.toLowerCase()) ||
      c.name.toLowerCase().includes(query.toLowerCase())
  );

  const currentItems = activeTrigger === "command" ? filteredCommands : fileSuggestions;

  const handleInputChange = useCallback((text: string, cursorPos: number) => {
    const textBeforeCursor = text.slice(0, cursorPos);
    const lastWord = textBeforeCursor.split(/\s+/).pop() || "";

    if (lastWord.startsWith("/")) {
      setActiveTrigger("command");
      setQuery(lastWord.slice(1));
      setIsOpen(true);
      setSelectedIndex(0);
    } else if (lastWord.startsWith("@")) {
      setActiveTrigger("file");
      setQuery(lastWord.slice(1));
      setIsOpen(true);
      setSelectedIndex(0);
    } else {
      setActiveTrigger(null);
      setQuery("");
      setIsOpen(false);
    }
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>, onSelect: (value: string) => void): boolean => {
      if (!isOpen || currentItems.length === 0) return false;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % currentItems.length);
        return true;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + currentItems.length) % currentItems.length);
        return true;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const selected = currentItems[selectedIndex];
        if (selected) {
          if (activeTrigger === "command") {
            const cmd = (selected as CommandItem).command;
            onSelect(`${cmd} `);
          } else if (activeTrigger === "file") {
            const file = (selected as FileItem).path;
            onSelect(`${file} `);
          }
        }
        setIsOpen(false);
        return true;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setIsOpen(false);
        return true;
      }

      return false;
    },
    [isOpen, currentItems, selectedIndex, activeTrigger]
  );

  return {
    isOpen,
    activeTrigger,
    currentItems,
    selectedIndex,
    setSelectedIndex,
    handleInputChange,
    handleKeyDown,
    close: () => setIsOpen(false),
  };
}
