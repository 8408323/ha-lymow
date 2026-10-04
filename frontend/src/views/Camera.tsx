import { useEffect, useRef, useState } from "react";
import { useEntity, useHass, useHassRef } from "../hass";
import { useT } from "../i18n";
import { useMower } from "../mower";
import { Button, Card, Icon, Segmented, cx } from "../ui";

type Source = "lan" | "snap" | "cloud";

export function CameraView() {
  const t = useT();
  const { ent } = useMower();
  const camId = ent("camera");
  const cam = useEntity(camId);
  const [source, setSource] = useState<Source>("lan");
  const stageRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string>("");

  return (
    <div className="ly-grid ly-grid--camera">
      <Card
        title={t("Camera")}
        icon="mdi:cctv"
        className="ly-card--wide"
        actions={
          <>
            <Segmented
              value={source}
              onChange={(s) => {
                setStatus("");
                setSource(s);
              }}
              options={[
                { value: "lan", label: t("Live"), icon: "mdi:lan" },
                { value: "snap", label: t("Snapshots"), icon: "mdi:camera-burst" },
                { value: "cloud", label: t("Cloud"), icon: "mdi:cloud-outline" },
              ]}
            />
            <Button variant="ghost" icon="mdi:fullscreen" title={t("Full screen")} onClick={() => stageRef.current?.requestFullscreen?.()} />
          </>
        }
      >
        <div className="ly-stage" ref={stageRef}>
          {source === "cloud" ? (
            <CloudStream onStatus={setStatus} />
          ) : !cam ? (
            <StageMsg icon="mdi:cctv-off" text={t("The camera entity isn't available.")} />
          ) : cam.state === "unavailable" ? (
            <StageMsg icon="mdi:lan-disconnect" text={t("The mower isn't reachable on your network. Try the Cloud view.")} />
          ) : source === "lan" ? (
            <LanStream entityId={camId!} />
          ) : (
            <Snapshots entityId={camId!} onStatus={setStatus} />
          )}
          {status && <div className="ly-stage__status">{status}</div>}
        </div>
        <p className="ly-muted ly-note">
          {t("Live streams over your home network. Snapshots use less bandwidth. Cloud works from anywhere but takes a few seconds to connect.")}
        </p>
      </Card>
      <DriveCard />
    </div>
  );
}

function StageMsg({ icon, text }: { icon: string; text: string }) {
  return (
    <div className="ly-stage__msg">
      <Icon name={icon} size={36} />
      {text}
    </div>
  );
}

function LanStream({ entityId }: { entityId: string }) {
  const hass = useHass();
  const ref = useRef<any>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.hass = hass;
      ref.current.stateObj = hass.states[entityId];
    }
  });
  return <ha-camera-stream ref={ref} class="ly-stage__media" muted controls={false} allow-exoplayer />;
}

function Snapshots({ entityId, onStatus }: { entityId: string; onStatus: (s: string) => void }) {
  const t = useT();
  const getHass = useHassRef();
  const [src, setSrc] = useState<string>();
  const [fps, setFps] = useState(2);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = () => {
      const started = performance.now();
      const token = getHass().states[entityId]?.attributes.access_token;
      const url = `/api/camera_proxy/${entityId}?token=${token}&_=${Date.now()}`;
      const img = new Image();
      img.onload = () => {
        if (!alive) return;
        setSrc(url);
        onStatus("");
        timer = window.setTimeout(tick, Math.max(0, 1000 / fps - (performance.now() - started)));
      };
      img.onerror = () => {
        if (!alive) return;
        onStatus(t("Waiting for the camera…"));
        timer = window.setTimeout(tick, 1000);
      };
      img.src = url;
    };
    tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [entityId, fps]);
  return (
    <>
      {src ? <img className="ly-stage__media" src={src} alt={t("Mower camera")} /> : <StageMsg icon="mdi:camera-outline" text={t("Loading…")} />}
      <div className="ly-stage__fps">
        <button type="button" aria-label={t("Fewer frames")} onClick={() => setFps(Math.max(0.5, fps - 0.5))}>
          −
        </button>
        {fps} fps
        <button type="button" aria-label={t("More frames")} onClick={() => setFps(Math.min(5, fps + 0.5))}>
          +
        </button>
      </div>
    </>
  );
}

/** AWS Kinesis Video WebRTC viewer, signalled through the session from lymow.start_video_session. */
function CloudStream({ onStatus }: { onStatus: (s: string) => void }) {
  const t = useT();
  const { callWithResponse } = useMower();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    let pc: RTCPeerConnection | undefined;
    let ws: WebSocket | undefined;
    let watchdog = 0;
    const enc = (o: unknown) => btoa(JSON.stringify(o));
    (async () => {
      onStatus(t("Connecting to the cloud camera…"));
      let session: { viewerWssUrl?: string; webrtcIceServers?: RTCIceServer[] };
      try {
        session = await callWithResponse("lymow", "start_video_session");
      } catch (e: any) {
        if (alive) onStatus(t("Couldn't start the cloud session: {error}", { error: e?.message ?? e }));
        return;
      }
      if (!alive) return;
      if (!session?.viewerWssUrl) return onStatus(t("The cloud camera isn't available right now."));
      pc = new RTCPeerConnection({ iceServers: session.webrtcIceServers ?? [], bundlePolicy: "max-bundle" });
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.ontrack = (e) => {
        const v = videoRef.current;
        if (!v) return;
        v.srcObject = e.streams[0] ?? new MediaStream([e.track]);
        v.play().catch(() => undefined);
      };
      pc.onconnectionstatechange = () => pc?.connectionState === "failed" && onStatus(t("The cloud connection failed. Try again."));
      ws = new WebSocket(session.viewerWssUrl);
      const early: RTCIceCandidateInit[] = [];
      pc.onicecandidate = (e) => e.candidate && ws?.readyState === 1 && ws.send(JSON.stringify({ action: "ICE_CANDIDATE", messagePayload: enc(e.candidate) }));
      ws.onopen = async () => {
        try {
          const offer = await pc!.createOffer();
          await pc!.setLocalDescription(offer);
          ws!.send(JSON.stringify({ action: "SDP_OFFER", messagePayload: enc({ type: "offer", sdp: offer.sdp }) }));
        } catch {
          if (alive) onStatus(t("The cloud connection failed. Try again."));
        }
      };
      ws.onmessage = async (m) => {
        let kind: string | undefined;
        let payload: any;
        try {
          const msg = JSON.parse(m.data);
          payload = JSON.parse(atob(msg.messagePayload));
          kind = msg.messageType ?? msg.action;
        } catch {
          return; // non-JSON keepalives from the signalling channel
        }
        if (kind === "SDP_ANSWER") {
          try {
            await pc!.setRemoteDescription({ type: "answer", sdp: payload.sdp });
          } catch {
            if (alive) onStatus(t("The cloud connection failed. Try again."));
            return;
          }
          // Candidates that arrived before the answer couldn't be applied yet.
          for (const c of early.splice(0)) await pc!.addIceCandidate(c).catch(() => undefined);
        } else if (kind === "ICE_CANDIDATE") {
          if (!pc!.remoteDescription) early.push(payload);
          else await pc!.addIceCandidate(payload).catch(() => undefined);
        }
      };
      ws.onerror = () => alive && onStatus(t("The signalling connection failed."));
      watchdog = window.setTimeout(async () => {
        const stats = await pc?.getStats();
        let frames = 0;
        stats?.forEach((r: any) => r.type === "inbound-rtp" && r.kind === "video" && (frames = r.framesDecoded ?? 0));
        if (alive && !frames) onStatus(t("No video from the mower — is it online?"));
      }, 25000);
    })();
    return () => {
      alive = false;
      window.clearTimeout(watchdog);
      ws?.close();
      pc?.close();
      if (videoRef.current) videoRef.current.srcObject = null;
    };
  }, [attempt]);
  return (
    <>
      <video ref={videoRef} className="ly-stage__media" autoPlay playsInline muted onLoadedData={() => onStatus("")} />
      <button type="button" className="ly-stage__retry" title={t("Reconnect")} aria-label={t("Reconnect")} onClick={() => setAttempt(attempt + 1)}>
        <Icon name="mdi:refresh" size={18} />
      </button>
    </>
  );
}

// ── Manual drive over Bluetooth ─────────────────────────────────────────────

const LINEAR_MAX = 0.5; // m/s — lymow.ble_drive limit
const ANGULAR_MAX = 0.6; // rad/s

function Joystick({ axis, label, onChange }: { axis: "y" | "x"; label: string; onChange: (v: number) => void }) {
  const [pos, setPos] = useState(0);
  const base = useRef<HTMLDivElement>(null);
  const active = useRef<number | null>(null);
  const R = 44;
  const move = (e: React.PointerEvent) => {
    if (active.current !== e.pointerId || !base.current) return;
    const r = base.current.getBoundingClientRect();
    const d = axis === "y" ? e.clientY - (r.top + r.height / 2) : e.clientX - (r.left + r.width / 2);
    const c = Math.max(-R, Math.min(R, d));
    setPos(c);
    onChange(-c / R); // up / left are positive
  };
  const release = () => {
    active.current = null;
    setPos(0);
    onChange(0);
  };
  return (
    <div className="ly-joy">
      <div
        ref={base}
        className={cx("ly-joy__base", `ly-joy__base--${axis}`)}
        onPointerDown={(e) => {
          active.current = e.pointerId;
          (e.currentTarget as Element).setPointerCapture(e.pointerId);
          move(e);
        }}
        onPointerMove={move}
        onPointerUp={release}
        onPointerCancel={release}
        role="slider"
        aria-label={label}
        aria-valuenow={Math.round((-pos / R) * 100)}
      >
        <div className="ly-joy__knob" style={{ transform: axis === "y" ? `translateY(${pos}px)` : `translateX(${pos}px)` }} />
      </div>
      <span className="ly-muted">{label}</span>
    </div>
  );
}

function DriveCard() {
  const t = useT();
  const { device } = useMower();
  const getHass = useHassRef();
  const vel = useRef({ lin: 0, ang: 0 });
  const active = useRef(false);
  const loop = useRef<Promise<void> | null>(null);
  const [err, setErr] = useState("");
  const [shown, setShown] = useState({ lin: 0, ang: 0 });

  // The drive loop outlives renders; read the held state through a ref.
  const held = useRef(device.held);
  held.current = device.held;
  const canControl = useRef(device.can_control);
  canControl.current = device.can_control;
  const send = (lin: number, ang: number) =>
    held.current || canControl.current === false
      ? Promise.resolve((setErr(held.current ? t("The mower is reconnecting. Try again in a moment.") : t("You can view this mower but not control it.")), false))
      : getHass()
      .callService("lymow", "ble_drive", { entity_id: device.entities.mower, linear: +(lin * LINEAR_MAX).toFixed(3), angular: +(ang * ANGULAR_MAX).toFixed(3), duration: 0.3 })
      .then(() => (setErr(""), true))
      .catch((e) => (setErr(e?.message ?? String(e)), false));

  // One request in flight at a time: each call holds the BLE link for its
  // duration, so firing on a timer would queue stale motions behind each other
  // and the release (stop) would only land after the backlog drained.
  const run = async (): Promise<void> => {
    while (active.current) {
      // A failing call (mower out of Bluetooth range) returns at once — back off
      // instead of hammering Home Assistant while the stick is held.
      if (!(await send(vel.current.lin, vel.current.ang))) await new Promise((r) => setTimeout(r, 1000));
    }
    await send(0, 0);
    loop.current = active.current ? run() : null;
  };

  const update = (part: Partial<{ lin: number; ang: number }>) => {
    vel.current = { ...vel.current, ...part };
    setShown(vel.current);
    active.current = vel.current.lin !== 0 || vel.current.ang !== 0;
    if (active.current && !loop.current) loop.current = run();
  };
  useEffect(
    () => () => {
      active.current = false;
    },
    [],
  );

  return (
    <Card title={t("Drive")} icon="mdi:gamepad-variant-outline">
      <p className="ly-muted">{t("Drive the mower by hand over Bluetooth. Home Assistant (or a Bluetooth proxy) has to be within range of the mower.")}</p>
      <div className="ly-drive">
        <Joystick axis="y" label={t("Forward / back")} onChange={(v) => update({ lin: v })} />
        <Joystick axis="x" label={t("Turn")} onChange={(v) => update({ ang: v })} />
      </div>
      <p className={cx("ly-drive__readout", err && "ly-drive__readout--err")}>
        {err || `${(shown.lin * LINEAR_MAX).toFixed(2)} m/s · ${(shown.ang * ANGULAR_MAX).toFixed(2)} rad/s`}
      </p>
    </Card>
  );
}
