window.__ModuleLoader__.load({
  id: "fishhome-dsh-meetings",
  factory(require) {
    const React = require("react");
    const id = "fishhome-meetings";

    function MeetingPanel() {
      const port = window.location.port === "15934" ? 16196 : 6196;
      return React.createElement("iframe", {
        title: "FishHome 组会会场",
        src: `http://127.0.0.1:${port}/meetings`,
        style: {display:"block", width:"100%", height:"100%", minHeight:"calc(100dvh - 60px)", border:0},
      });
    }

    function Icon() {
      return React.createElement("span", {"aria-hidden":true}, "◉");
    }

    function apply(ctx) {
      ctx.effect(function* () {
        yield ctx.slots.inject("main", () => ctx.slots.register({name:"main", key:id}, MeetingPanel));
        yield ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
          name:"sidebar.panellist", id, label:"FishHome 组会会场",
        }, Icon));
        if (window.location.hash === "#" + id) {
          ctx.layout.selectPanel(id);
          window.history.replaceState(null, "", window.location.pathname + window.location.search);
        }
      });
    }

    return {apply, inject:["slots","layout"]};
  },
});
