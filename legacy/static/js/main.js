function toggleUserMenu(){
    document.getElementById('user-menu').classList.toggle('open');
}

document.addEventListener('click',function(e){
    var menu=document.getElementById('user-menu');
    if(menu&&!menu.contains(e.target)){
        menu.classList.remove('open');
    }
});

document.addEventListener('DOMContentLoaded', function(){
    // Add logic if needed for global shortcuts
});
const saveButton = document.getElementById("saveEntry");

if (saveButton) {

    saveButton.addEventListener("click", async () => {

        const title = document.getElementById("title").value;

        const content = document.getElementById("content").value;

        const entry_type = document.getElementById("entryType").value;

        const tags = document.getElementById("tags").value;

        const mood = document.getElementById("mood").value;

        const response = await fetch("/api/entries/", {

            method: "POST",

            headers: {

                "Content-Type": "application/json"

            },

            body: JSON.stringify({

                title,

                content,

                entry_type,

                tags,

                mood

            })

        });

        if (response.ok) {

            window.location = "/";

        } else {

            alert("Failed to save entry.");

        }

    });

}
