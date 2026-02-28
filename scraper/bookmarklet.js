/**
 * CarGurus Inventory Scraper Bookmarklet
 * =======================================
 * Run this on a CarGurus dealer search results page.
 * Extracts vehicle data + photo URLs, downloads a CSV
 * compatible with the pipeline's "Upload CSV" feature.
 *
 * To use: minify this file, wrap in javascript:(async function(){...})(),
 * then save as a browser bookmark.
 */
(async function __scrape__() {

  /* -- Status panel -- */
  var panel = document.createElement('div');
  panel.id = '_cgP';
  Object.assign(panel.style, {
    position:'fixed', bottom:'20px', right:'20px', zIndex:'999999',
    background:'#151929', border:'1px solid #2a3550', borderRadius:'12px',
    padding:'18px 24px', fontFamily:'system-ui,sans-serif', color:'#eaf0ff',
    minWidth:'260px', boxShadow:'0 8px 40px rgba(0,0,0,.5)'
  });
  panel.innerHTML = [
    '<div id=_t style="font-weight:700;font-size:14px;margin-bottom:6px">\u{1F50D} Scraping...</div>',
    '<div id=_s style="font-size:12px;color:#8892a8">Starting...</div>',
    '<div id=_c style="font-size:32px;font-weight:700;color:#5ce0d8;margin:4px 0">0</div>',
    '<div id=_l style="font-size:11px;color:#667">vehicles found</div>',
    '<div style="background:#1a2036;border-radius:6px;height:6px;margin-top:10px;overflow:hidden">',
    '<div id=_b style="height:100%;width:0%;background:linear-gradient(90deg,#5ce0d8,#7c8aff);border-radius:6px;transition:width .3s"></div></div>'
  ].join('');
  document.body.appendChild(panel);

  function $(id) { return document.getElementById(id); }
  function delay(ms) { return new Promise(function(r) { setTimeout(r, ms); }); }

  var DEALERS = [
    { match: 'San Antonio Dodge', name: 'SA CDJR' },
    { match: 'San-Antonio-Dodge', name: 'SA CDJR' },
    { match: 'Ancira', name: 'Ancira CJD' },
    { match: 'Bluebonnet Chrysler', name: 'Bluebonnet CDR' },
    { match: 'Bluebonnet-Chrysler', name: 'Bluebonnet CDR' },
    { match: 'Bluebonnet Jeep', name: 'Bluebonnet Jeep' },
    { match: 'Bluebonnet-Jeep', name: 'Bluebonnet Jeep' },
    { match: 'Boerne', name: 'Boerne DCJR' },
    { match: 'Steele North Star', name: 'Steele North Star' },
    { match: 'Steele-North-Star', name: 'Steele North Star' },
    { match: 'Benson', name: "Benson's IPAC" },
    { match: 'Gunn', name: 'Gunn CDJR' }
  ];
  var SRC = 'Unknown Dealer';
  var title = document.title + ' ' + window.location.href;
  for (var di = 0; di < DEALERS.length; di++) {
    if (title.indexOf(DEALERS[di].match) >= 0) { SRC = DEALERS[di].name; break; }
  }
  $('_t').textContent = '\u{1F50D} Scraping: ' + SRC;

  var allVehicles = [];
  var seenVINs = {};
  var pageNum = 0;

  /* -- Field labels for boundary-aware parsing -- */
  var LABELS = ['Year','Make','Model','Body type','Doors','Drivetrain','Engine',
    'Exterior color','Combined gas mileage','Fuel type','Interior color',
    'Transmission','Mileage','Stock #','VIN'];

  function getField(txt, label) {
    var idx = txt.indexOf(label + ':');
    if (idx < 0) return '';
    if (label === 'Mileage') {
      var re = new RegExp('(?:^|[^a-z])' + label + ':\\s*', 'gi');
      var m;
      while ((m = re.exec(txt)) !== null) {
        var before = txt.substring(Math.max(0, m.index - 5), m.index).toLowerCase();
        if (before.indexOf('gas') < 0) { idx = m.index; break; }
      }
    }
    var start = txt.indexOf(':', idx) + 1;
    var val = txt.substring(start).replace(/^\s+/, '');
    var earliest = val.length;
    for (var i = 0; i < LABELS.length; i++) {
      var li = val.indexOf(LABELS[i] + ':');
      if (li > 0 && li < earliest) earliest = li;
    }
    return val.substring(0, earliest).trim();
  }

  /* -- Extract vehicles from current DOM -- */
  function extractPage() {
    var results = [];
    var links = document.querySelectorAll('a[href*="/details/"]');

    for (var i = 0; i < links.length; i++) {
      try {
        /* -- Get the detail URL -- */
        var detailLink = links[i];
        var href = detailLink.getAttribute('href') || '';
        var detailUrl = '';
        if (href.indexOf('/details/') >= 0) {
          var idMatch = href.match(/\/details\/(\d+)/);
          if (idMatch) {
            detailUrl = 'https://www.cargurus.com/Cars/details/' + idMatch[1];
          }
        }

        /* -- Walk up to listing container -- */
        var el = detailLink;
        for (var u = 0; u < 12; u++) {
          if (!el.parentElement) break;
          el = el.parentElement;
          if (el.textContent.length > 300 && el.textContent.indexOf('VIN:') >= 0) break;
        }
        var txt = el.textContent;

        /* -- VIN (required, dedup key) -- */
        var vinM = txt.match(/VIN:\s*([A-HJ-NPR-Z0-9]{17})/i);
        if (!vinM) continue;
        var vin = vinM[1];
        if (seenVINs[vin]) continue;
        seenVINs[vin] = true;

        /* -- Structured fields -- */
        var year  = getField(txt, 'Year');
        var make  = getField(txt, 'Make');
        var model = getField(txt, 'Model');
        var color = getField(txt, 'Exterior color');
        var drive = getField(txt, 'Drivetrain');
        var eng   = getField(txt, 'Engine');
        var fuel  = getField(txt, 'Fuel type');
        var body  = getField(txt, 'Body type');
        var stock = getField(txt, 'Stock #');

        /* -- Mileage (actual odometer, NOT "Combined gas mileage") -- */
        var mileage = 0;
        var miRe = /(?:^|[^a-z])Mileage:\s*([\d,]+)/gi;
        var miMatch;
        while ((miMatch = miRe.exec(txt)) !== null) {
          var before5 = txt.substring(Math.max(0, miMatch.index - 5), miMatch.index).toLowerCase();
          if (before5.indexOf('gas') < 0) {
            mileage = parseInt(miMatch[1].replace(/,/g, '')) || 0;
            break;
          }
        }

        /* -- Trim: text between "Learn more..." heading and miles/price -- */
        var trim = '';
        var learnMore = 'Learn more about this ' + year + ' ' + make + ' ' + model;
        var lmIdx = txt.indexOf(learnMore);
        if (lmIdx >= 0) {
          var afterLM = txt.substring(lmIdx + learnMore.length);
          var trimMatch = afterLM.match(/^\s*(.+?)\s*(?:[\d,]+\s*miles|\$[\d,]+)/i);
          if (trimMatch) {
            trim = trimMatch[1]
              .replace(/Save this listing/gi, '')
              .replace(/Preparing for a close up.*/i, '')
              .replace(/Photos coming soon/gi, '')
              .replace(/Manufacturer certified/gi, '')
              .replace(/New car/gi, '')
              .trim();
          }
        }
        if (!trim) {
          var headings = el.querySelectorAll('h4, h3, h2');
          for (var h = 0; h < headings.length; h++) {
            var ht = headings[h].textContent.trim();
            if (ht.indexOf(year) >= 0 && model && ht.indexOf(model) >= 0) {
              var mi2 = ht.indexOf(model);
              trim = ht.substring(mi2 + model.length).trim();
              if (trim) break;
            }
          }
        }

        /* -- MSRP detection -- */
        var msrp = 0;

        var msrpM = txt.match(/MSRP[:\s]*\$([\d,]+)/i);
        if (msrpM) {
          msrp = parseInt(msrpM[1].replace(/,/g, '')) || 0;
        }

        if (!msrp) {
          var listM = txt.match(/List\s*Price[:\s]*\$([\d,]+)/i);
          if (listM) msrp = parseInt(listM[1].replace(/,/g, '')) || 0;
        }

        if (!msrp) {
          var strikes = el.querySelectorAll('s, strike, del, [style*="line-through"], [class*="strikethrough"], [class*="original"], [class*="msrp"], [class*="MSRP"], [class*="listPrice"]');
          for (var si = 0; si < strikes.length; si++) {
            var stxt = strikes[si].textContent;
            var spm = stxt.match(/\$([\d,]+)/);
            if (spm) {
              var sv = parseInt(spm[1].replace(/,/g, ''));
              if (sv > 5000 && sv < 300000) { msrp = sv; break; }
            }
          }
        }

        if (!msrp) {
          var allEls = el.querySelectorAll('[data-msrp], [data-list-price], [data-original-price]');
          for (var ai = 0; ai < allEls.length; ai++) {
            var dv = allEls[ai].getAttribute('data-msrp') || allEls[ai].getAttribute('data-list-price') || allEls[ai].getAttribute('data-original-price');
            if (dv) { msrp = parseInt(dv.replace(/[^0-9]/g, '')) || 0; break; }
          }
        }

        /* -- Sale Price (first dollar amount > $5000 that isn't the MSRP label) -- */
        var price = 0;
        var priceMatches = txt.match(/\$([\d,]+)/g);
        if (priceMatches) {
          for (var p = 0; p < priceMatches.length; p++) {
            var val = parseInt(priceMatches[p].replace(/[$,]/g, ''));
            if (val > 5000 && val < 250000) { price = val; break; }
          }
        }

        if (msrp > 0 && msrp === price) msrp = 0;

        /* -- Deal rating -- */
        var dealM = txt.match(/(Great|Good|Fair|High|No)\s+Deal/i);

        /* -- Photo URLs from listing card -- */
        var photos = [];
        var imgs = el.querySelectorAll('img');
        for (var pi = 0; pi < imgs.length; pi++) {
          var src = imgs[pi].getAttribute('src') || imgs[pi].getAttribute('data-src') || '';
          if (src && src.length > 30 &&
              src.indexOf('logo') < 0 &&
              src.indexOf('icon') < 0 &&
              src.indexOf('placeholder') < 0 &&
              src.indexOf('data:') < 0 &&
              src.indexOf('.svg') < 0 &&
              src.indexOf('avatar') < 0 &&
              src.indexOf('dealer') < 0 &&
              src.indexOf('badge') < 0) {
            /* Try to get highest resolution by removing resize params */
            var hiRes = src
              .replace(/\/t_[^/]+\//, '/')
              .replace(/_\d+x\d+/, '')
              .replace(/\?.*$/, '');
            if (photos.indexOf(hiRes) < 0) photos.push(hiRes);
          }
        }

        /* Also check picture/source elements for modern image formats */
        var sources = el.querySelectorAll('picture source, img[srcset]');
        for (var si2 = 0; si2 < sources.length; si2++) {
          var srcset = sources[si2].getAttribute('srcset') || '';
          var srcParts = srcset.split(',');
          for (var sp = 0; sp < srcParts.length; sp++) {
            var srcUrl = srcParts[sp].trim().split(/\s+/)[0];
            if (srcUrl && srcUrl.length > 30 &&
                srcUrl.indexOf('logo') < 0 &&
                srcUrl.indexOf('icon') < 0 &&
                srcUrl.indexOf('data:') < 0) {
              var hiRes2 = srcUrl.replace(/\/t_[^/]+\//, '/').replace(/\?.*$/, '');
              if (photos.indexOf(hiRes2) < 0) photos.push(hiRes2);
            }
          }
        }

        results.push({
          year: parseInt(year) || 0, make: make, model: model, trim: trim,
          msrp: msrp, price: price, mileage: mileage, stock: stock, vin: vin,
          color: color, drivetrain: drive, engine: eng,
          fuelType: fuel, bodyType: body,
          deal: dealM ? dealM[0] : '',
          url: detailUrl || (vin ? 'https://www.google.com/search?q=' + vin : ''),
          photos: photos.join('|')
        });
      } catch(e) {}
    }
    return results;
  }

  /* -- Pagination -- */
  function getPageInfo() {
    var m = document.body.textContent.match(/Page\s+(\d+)\s+of\s+(\d+)/i);
    return m ? { cur: parseInt(m[1]), tot: parseInt(m[2]) } : null;
  }

  function clickNext() {
    var allEls = document.querySelectorAll('button, a, [role="button"]');
    for (var i = 0; i < allEls.length; i++) {
      var el = allEls[i];
      var t = (el.textContent || '').trim().toLowerCase();
      var aria = (el.getAttribute('aria-label') || '').toLowerCase();
      if (t === 'next page' || t === 'next' || aria === 'next page' || aria === 'next' || aria === 'go to next page') {
        el.click();
        return true;
      }
    }
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    while (walker.nextNode()) {
      var node = walker.currentNode;
      if (node.textContent.trim().toLowerCase() === 'next page') {
        var clickTarget = node.parentElement;
        if (clickTarget) { clickTarget.click(); return true; }
      }
    }
    return false;
  }

  function firstVIN() {
    var links = document.querySelectorAll('a[href*="/details/"]');
    for (var i = 0; i < links.length; i++) {
      var el = links[i];
      for (var u = 0; u < 10; u++) {
        if (!el.parentElement) break;
        el = el.parentElement;
        if (el.textContent.indexOf('VIN:') >= 0) break;
      }
      var m = el.textContent.match(/VIN:\s*([A-HJ-NPR-Z0-9]{17})/i);
      if (m) return m[1];
    }
    return null;
  }

  async function waitNewPage(oldVin) {
    for (var i = 0; i < 30; i++) {
      await delay(600);
      var nv = firstVIN();
      if (nv && nv !== oldVin) return true;
      var pi = getPageInfo();
      if (pi && pi.cur > pageNum) return true;
    }
    return false;
  }

  /* === MAIN LOOP === */
  var pi = getPageInfo();
  var totalPages = pi ? pi.tot : 1;

  while (true) {
    pageNum++;
    var found = extractPage();
    allVehicles = allVehicles.concat(found);

    pi = getPageInfo();
    var cur = pi ? pi.cur : pageNum;
    var tot = pi ? pi.tot : totalPages;

    $('_s').textContent = 'Page ' + cur + ' of ' + tot + ' (' + found.length + ' on this page)';
    $('_c').textContent = String(allVehicles.length);
    $('_b').style.width = Math.round(cur / tot * 100) + '%';

    if (pi && pi.cur >= pi.tot) break;
    if (found.length === 0 && pageNum > 1) break;
    if (pageNum > 60) break;

    var oldV = firstVIN();
    var clicked = clickNext();
    if (!clicked) {
      $('_s').textContent = 'Could not find Next button - stopped at page ' + cur;
      $('_s').style.color = '#fbbf24';
      break;
    }
    var ok = await waitNewPage(oldV);
    if (!ok) {
      $('_s').textContent = 'Page did not change - stopped at page ' + cur;
      $('_s').style.color = '#fbbf24';
      break;
    }
    await delay(800);
  }

  /* === GROUP BY MODEL+TRIM, LOWEST SALE PRICE === */
  var groups = {};
  for (var i = 0; i < allVehicles.length; i++) {
    var v = allVehicles[i];
    var key = v.make + '|' + v.model + '|' + v.trim;
    if (!groups[key] || (v.price > 0 && (v.price < groups[key].price || groups[key].price === 0))) {
      groups[key] = v;
    }
  }
  var summary = [];
  for (var k in groups) { if (groups.hasOwnProperty(k)) summary.push(groups[k]); }
  summary.sort(function(a, b) {
    if (a.make !== b.make) return a.make < b.make ? -1 : 1;
    if (a.model !== b.model) return a.model < b.model ? -1 : 1;
    return (a.price || 999999) - (b.price || 999999);
  });

  /* === Count MSRP and photo hits === */
  var msrpCount = 0;
  var photoCount = 0;
  for (var i = 0; i < allVehicles.length; i++) {
    if (allVehicles[i].msrp > 0) msrpCount++;
    if (allVehicles[i].photos) photoCount++;
  }

  /* === BUILD & DOWNLOAD CSV === */
  var hdr = ['Source','Year','Make','Model','Trim','MSRP','Sale Price','Mileage','Stock','VIN','Color','Drivetrain','Deal','URL','Photos','Updated'];
  var today = new Date().toISOString().split('T')[0];
  var lines = [hdr.join(',')];
  for (var i = 0; i < summary.length; i++) {
    var v = summary[i];
    var q = function(s) { return '"' + String(s || '').replace(/"/g, '""') + '"'; };
    lines.push([
      q(SRC), v.year, q(v.make), q(v.model), q(v.trim),
      v.msrp || '', v.price, v.mileage,
      q(v.stock), v.vin, q(v.color), q(v.drivetrain),
      q(v.deal), q(v.url), q(v.photos), today
    ].join(','));
  }
  var csv = lines.join('\n');
  var blob = new Blob([csv], { type: 'text/csv' });
  var url2 = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url2;
  a.download = SRC.replace(/\s+/g, '_') + '_NewByTrim_' + today + '.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url2);

  /* === DONE === */
  $('_t').textContent = '\u2705 Complete';
  var msrpNote = msrpCount > 0 ? ' | ' + msrpCount + ' MSRPs found' : ' | MSRP not on search page';
  var photoNote = ' | ' + photoCount + '/' + allVehicles.length + ' with photos';
  $('_s').textContent = summary.length + ' trims from ' + allVehicles.length + ' vehicles' + msrpNote + photoNote;
  $('_s').style.color = '#34d399';
  $('_c').textContent = String(summary.length);
  $('_l').textContent = 'unique model/trims downloaded';
  $('_b').style.width = '100%';

  setTimeout(function() { panel.style.transition = 'opacity .5s'; panel.style.opacity = '0.5'; }, 8000);
})()
