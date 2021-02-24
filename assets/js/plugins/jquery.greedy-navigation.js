/*
GreedyNav.js - http://lukejacksonn.com/actuate
Licensed under the MIT license - http://opensource.org/licenses/MIT
Copyright (c) 2015 Luke Jackson
*/

$(function() {

  var $btn = $("nav.greedy-nav .greedy-nav__toggle");
  var $vlinks = $("nav.greedy-nav .visible-links");
  var $hlinks = $("nav.greedy-nav .hidden-links");
  var $nav = $("nav.greedy-nav");
  var $logo = $('nav.greedy-nav .site-logo');
  var $logoImg = $('nav.greedy-nav .site-logo img');
  var $title = $("nav.greedy-nav .site-title");
  var $search = $('nav.greedy-nav button.search__toggle');

  var navbarSpace = 0;
  var winWidth = $( window ).width();

  // measure width of all navbar links

  function addWidth(i, w) {
    navbarSpace += w;
  }

  $vlinks.children().outerWidth(addWidth);
  $hlinks.children().outerWidth(addWidth);

  // function to move links to hidden menu
  function check() {

  // measure available space in navigation bar

    var availableSpace = /* nav */ $nav.innerWidth()
                       - /* logo */ ($logo.length !== 0 ? $logo.outerWidth(true) : 0)
                       - /* title */ $title.outerWidth(true)
                       - /* search */ ($search.length !== 0 ? $search.outerWidth(true) : 0);

    if (navbarSpace > availableSpace) {
      $vlinks.children().prependTo($hlinks);
      $btn.removeClass('hidden')
    } else {
      $hlinks.children().appendTo($vlinks);
      $btn.addClass('hidden')
    }
  }

  // Window listeners
  $(window).resize(function() {
    check();
  });

  $btn.on('click', function() {
    $hlinks.toggleClass('hidden');
    $btn.toggleClass('close');
  });

  // Initial check
  if($logoImg.length !== 0){
    // check if logo is not loaded
    if(!($logoImg[0].complete || $logoImg[0].naturalWidth !== 0)){
      // if logo is not loaded wait for logo to load or fail to check
      $logoImg.one("load error", check);
    // if logo is already loaded just check
    } else check();
  // if page does not have a logo just check
  } else check();

});
