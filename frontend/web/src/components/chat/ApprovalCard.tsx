import React from "react";
import { ShieldAlert, Check, X } from "lucide-react";
import { ApprovalRequestEvent } from "../../types/events";

interface ApprovalCardProps {
  request: ApprovalRequestEvent;
  onRespond: (requestId: string, approved: boolean) => void;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({ request, onRespond }) => {
  return (
    <div className="my-3 p-4 rounded-lg border-2 border-amber-500/40 bg-amber-950/20 backdrop-blur-sm text-xs">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-full bg-amber-500/20 text-amber-400">
          <ShieldAlert className="w-5 h-5" />
        </div>
        <div className="flex-1 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-amber-300 text-sm">
              Approval Required: {request.action}
            </span>
            <span className="font-mono text-slate-500 text-[11px]">{request.request_id}</span>
          </div>

          <p className="text-slate-300">
            The agent requested an operation that requires user confirmation under active security policy.
          </p>

          <pre className="p-2.5 rounded bg-slate-950 border border-slate-800 font-mono text-[11px] text-slate-300 overflow-x-auto">
            {JSON.stringify(request.details, null, 2)}
          </pre>

          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={() => onRespond(request.request_id, true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-medium transition shadow-sm"
            >
              <Check className="w-3.5 h-3.5" />
              <span>Approve Operation</span>
            </button>
            <button
              onClick={() => onRespond(request.request_id, false)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-rose-600/80 hover:bg-rose-600 text-white font-medium transition shadow-sm"
            >
              <X className="w-3.5 h-3.5" />
              <span>Deny</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
