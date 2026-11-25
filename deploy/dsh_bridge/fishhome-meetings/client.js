window.__ModuleLoader__.load({
  id: "fishhome-dsh-meetings",
  factory(require) {
    const React = require("react");
    const panels = [
      {id:"fishhome-meetings", name:"FishHome 组会会场", path:"/meetings", icon:"◉"},
      {id:"fishhome-owner-work", name:"FishHome 主人工作台", path:"/owner-work", icon:"▣"},
    ];

    function currentTheme() {
      return document.body && document.body.hasAttribute("data-ds-dark-theme") ? "dark" : "light";
    }

    function PanelFrame({panel}) {
      const port = window.location.port === "15934" ? 16196 : 6196;
      const origin = `http://127.0.0.1:${port}`;
      const frameRef = React.useRef(null);
      const initialSrc = React.useMemo(() => {
        return `${origin}${panel.path}?embed=dsh&theme=${currentTheme()}`;
      }, [origin, panel.path]);

      const pushTheme = React.useCallback(() => {
        if (!frameRef.current || !frameRef.current.contentWindow) return;
        frameRef.current.contentWindow.postMessage({type:"fishhome-theme", theme:currentTheme()}, origin);
      }, [origin]);

      React.useEffect(() => {
        if (!document.body) return;
        const obs = new MutationObserver(pushTheme);
        obs.observe(document.body, {attributes:true, attributeFilter:["data-ds-dark-theme"]});
        return () => obs.disconnect();
      }, [pushTheme]);

      return React.createElement("iframe", {
        ref: frameRef,
        title: panel.name,
        src: initialSrc,
        onLoad: pushTheme,
        style: {display:"block", width:"100%", height:"100%", minHeight:"calc(100dvh - 60px)", border:0},
      });
    }

    function apply(ctx) {
      ctx.effect(function* () {
        for (const panel of panels) {
          yield ctx.slots.inject("main", () => ctx.slots.register({name:"main", key:panel.id}, () => React.createElement(PanelFrame, {panel})));
          yield ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
            name:"sidebar.panellist", id:panel.id, label:panel.name,
          }, () => React.createElement("span", {"aria-hidden":true}, panel.icon)));
        }
        const selected = panels.find(panel => window.location.hash === "#" + panel.id);
        if (selected) {
          ctx.layout.selectPanel(selected.id);
          window.history.replaceState(null, "", window.location.pathname + window.location.search);
        }
      });
    }

    return {apply, inject:["slots","layout"]};
  },
});
