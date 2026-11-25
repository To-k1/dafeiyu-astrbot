window.__ModuleLoader__.load({
  id: "fishhome-dsh-meetings",
  factory(require) {
    const React = require("react");
    const panels = [
      {id:"fishhome-meetings", name:"FishHome 组会会场", path:"/meetings", icon:"◉"},
      {id:"fishhome-owner-work", name:"FishHome 主人工作台", path:"/owner-work", icon:"▣"},
    ];

    function renderPanel(panel) {
      const port = window.location.port === "15934" ? 16196 : 6196;
      return React.createElement("iframe", {
        title: panel.name,
        src: `http://127.0.0.1:${port}${panel.path}`,
        style: {display:"block", width:"100%", height:"100%", minHeight:"calc(100dvh - 60px)", border:0},
      });
    }

    function apply(ctx) {
      ctx.effect(function* () {
        for (const panel of panels) {
          yield ctx.slots.inject("main", () => ctx.slots.register({name:"main", key:panel.id}, () => renderPanel(panel)));
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
