/* ==========================================================================
   JavaScript scripts for site
   ========================================================================== */

onready = function() {

  // Search

  var initial_content = document.getElementsByClassName("initial-content").item(0);
  var search_content = document.getElementsByClassName("search-content").item(0);

  if (search_content) {

    // Search toggle
    var search_toggle = document.getElementsByClassName("search__toggle").item(0);
    var search_content_input = document.getElementById("search");

    search_toggle.onclick = function() {
      search_content.classList.toggle("is--visible");
      initial_content.classList.toggle("is--hidden");
      // set focus on input
      setTimeout(function() {
        search_content_input.focus();
      }, 400);
    };

    // Close search with ESC key
    document.onkeyup = function(e) {
      if (e.keyCode === 27) {
        if (initial_content.classList.contains("is--hidden")) {
          search_content.classList.toggle("is--visible");
          initial_content.classList.toggle("is--hidden");
        }
      }
    };

  }

  // Add anchors for all headings with id

  var page__content = document.getElementsByClassName("page__content").item(0);
  var headings = page__content.querySelectorAll("h1, h2, h3, h4, h5, h6");
  headings.forEach($heading => {
    var id = $heading.getAttribute("id");
    if (id) {
      var anchor = document.createElement("a");
      anchor.className = "header-link";
      anchor.href = "#" + id;
      anchor.innerHTML = "<span class=\"sr-only\">Permalink</span><i class=\"fas fa-link\"></i>";
      anchor.title = "Permalink";
      $heading.append(anchor);
    }
  });
};

document.addEventListener("DOMContentLoaded", onready);
