// <lymow-panel>: the custom element Home Assistant mounts for the sidebar panel.
// HA sets `hass` (on every state change), `narrow`, `route` and `panel`.

import { createRoot, type Root } from "react-dom/client";
import { App } from "./App";
import { HassStore, StoreContext, type Hass } from "./hass";
import css from "./styles.css?inline";

export interface Route {
  prefix: string;
  path: string;
}

class LymowPanel extends HTMLElement {
  private store = new HassStore();
  private root?: Root;
  private _narrow = false;
  private _route: Route = { prefix: "/lymow", path: "" };

  set hass(hass: Hass) {
    const first = !this.store.hass;
    this.store.set(hass);
    if (first) this.render();
  }

  set narrow(v: boolean) {
    this._narrow = v;
    this.render();
  }

  set route(r: Route) {
    this._route = r;
    this.render();
  }

  connectedCallback() {
    this.render();
  }

  disconnectedCallback() {
    this.root?.unmount();
    this.root = undefined;
  }

  private render() {
    if (!this.isConnected || !this.store.hass) return;
    if (!this.root) {
      const shadow = this.shadowRoot ?? this.attachShadow({ mode: "open" });
      shadow.innerHTML = `<style>${css}</style><div class="ly-root"></div>`;
      this.root = createRoot(shadow.querySelector(".ly-root")!);
    }
    this.root.render(
      <StoreContext.Provider value={this.store}>
        <App narrow={this._narrow} route={this._route} host={this} />
      </StoreContext.Provider>,
    );
  }
}

if (!customElements.get("lymow-panel")) customElements.define("lymow-panel", LymowPanel);
