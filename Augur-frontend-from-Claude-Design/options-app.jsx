// options-app.jsx — design canvas that hosts the three Augur directions.

const AUGUR_ART_W = 1440;
const AUGUR_ART_H = 1080;

function OptionsApp() {
  return (
    <DesignCanvas title="Augur · three directions" subtitle="Click any artboard to focus · drag to reorder">
      <DCSection id="directions" title="Three directions" subtitle="Same data, same palette (Vindingur), different metaphors. Each is interactive — click into specimens / rings / beds to expand.">
        <DCArtboard id="herbarium" label="A · Herbarium  ·  pressed-specimen plate"
                    width={AUGUR_ART_W} height={AUGUR_ART_H}>
          <HerbariumOption />
        </DCArtboard>
        <DCArtboard id="astrolabe" label="B · Astrolabe  ·  polar almanac"
                    width={AUGUR_ART_W} height={AUGUR_ART_H}>
          <AstrolabeOption />
        </DCArtboard>
        <DCArtboard id="garden" label="C · Walled Garden  ·  cultivated plan"
                    width={AUGUR_ART_W} height={AUGUR_ART_H}>
          <GardenOption />
        </DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<OptionsApp />);
