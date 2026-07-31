import { useCallback, useEffect, useRef, useState } from "react";

import { fetchFromAPI } from "../utils/api.utils";

// Estados canónicos del endpoint: not_started | pending | processing | completed | error
const POLL_INTERVAL_MS = 3000;
const POLLING_MAX_ATTEMPTS = 20;
// El job se persiste en DynamoDB tras el POST; `not_started` (o un `completed` de un
// job previo) solo aparece de forma transitoria por la consistencia eventual del GSI.
const POLLING_MAX_NOT_STARTED = 3;

/**
 * Polling del worker async de re-sync de custom objects (mismo endpoint/job_type
 * que usa "publicar cotización"). El endpoint POST devuelve el `job_id` encolado;
 * este hook lo espera hasta `completed`.
 *
 * @param {string} dealId
 * @returns {{
 *   status: (null|'pending'|'processing'|'completed'|'error'),
 *   data: {contenedores:number, elementos:number},
 *   waitForCompletion: (jobId:string) => Promise<{contenedores:number, elementos:number}>,
 *   reset: () => void
 * }}
 */
export const useCustomObjectsSync = (dealId) => {
  const [status, setStatus] = useState(null);
  const [data, setData] = useState({ contenedores: 0, elementos: 0 });

  // Cada waitForCompletion incrementa la sesión; los ticks en vuelo se autoinvalidan
  // si dejan de ser la sesión activa (remonte del card, doble invocación).
  const sessionIdRef = useRef(0);
  const timeoutRef = useRef(null);

  const stop = useCallback(() => {
    sessionIdRef.current += 1;
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  useEffect(() => () => stop(), [stop]);

  const reset = useCallback(() => {
    stop();
    setStatus(null);
    setData({ contenedores: 0, elementos: 0 });
  }, [stop]);

  const waitForCompletion = useCallback(
    (expectedJobId) =>
      new Promise((resolve, reject) => {
        stop();
        sessionIdRef.current += 1;
        const sessionId = sessionIdRef.current;
        let attempts = 0;
        let notStartedAttempts = 0;

        setStatus("pending");
        setData({ contenedores: 0, elementos: 0 });

        const isCurrent = () => sessionIdRef.current === sessionId;

        const finishError = (msg) => {
          if (isCurrent()) setStatus("error");
          stop();
          reject(new Error(msg || "sync_error"));
        };

        const schedule = () => {
          if (!isCurrent()) return;
          timeoutRef.current = setTimeout(() => {
            timeoutRef.current = null;
            if (isCurrent()) tick();
          }, POLL_INTERVAL_MS);
        };

        const tick = async () => {
          if (!isCurrent()) return;

          let response;
          try {
            response = await fetchFromAPI(
              `/api/v1/deals/${dealId}/custom-objects/status`,
              { method: "GET" }
            );
          } catch (error) {
            if (!isCurrent()) return;
            return finishError(error?.message);
          }
          if (!isCurrent()) return;

          if (!response.success || !response.data) {
            return finishError("respuesta inválida del servidor");
          }

          const {
            status: s,
            contenedores,
            elementos,
            job_id: respJobId,
            message,
          } = response.data;

          // Anti-carrera: mientras el GSI no vea nuestro job, el endpoint puede
          // devolver el job anterior (p.ej. un publish ya completado). Solo aceptamos
          // el resultado cuando corresponde al job_id que devolvió el POST.
          const isForExpected =
            !expectedJobId || !respJobId || respJobId === expectedJobId;

          if (s === "completed" && isForExpected) {
            const result = {
              contenedores: contenedores || 0,
              elementos: elementos || 0,
            };
            if (isCurrent()) {
              setStatus("completed");
              setData(result);
            }
            stop();
            return resolve(result);
          }

          if (s === "error") {
            return finishError(message);
          }

          if (s === "not_started" || (s === "completed" && !isForExpected)) {
            notStartedAttempts += 1;
            if (notStartedAttempts >= POLLING_MAX_NOT_STARTED) {
              return finishError("el job de sincronización no se inició");
            }
            return schedule();
          }
          notStartedAttempts = 0;

          if (s === "pending" || s === "processing") {
            if (isCurrent()) {
              setStatus(s);
              setData({
                contenedores: contenedores || 0,
                elementos: elementos || 0,
              });
            }
            attempts += 1;
            if (attempts >= POLLING_MAX_ATTEMPTS) {
              return finishError("la sincronización está tardando demasiado");
            }
            return schedule();
          }

          return finishError("estado no reconocido");
        };

        tick();
      }),
    [dealId, stop]
  );

  return { status, data, waitForCompletion, reset };
};
